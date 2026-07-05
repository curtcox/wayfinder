"""Structured LLM output tests with a local stub server."""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig
from wayfinder.llm.errors import LLMError
from wayfinder.llm.structured import generate_brain_recommendation


class _ResponseQueue:
    items: list[str] = []


def _valid_recommendation() -> dict[str, Any]:
    return {
        "recommendation_type": "action",
        "summary": "Run true.",
        "goal_status": "running",
        "confidence": 0.85,
        "action": {
            "kind": "shell",
            "title": "Run true",
            "shell": {
                "argv": ["true"],
                "command_for_display": "true",
                "cwd": "file:/tmp/work",
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 30,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [],
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_true",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 1,
        },
        "risk": {
            "level": "low",
            "classes": ["execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
    }


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        payload = _ResponseQueue.items.pop(0)
        body = json.dumps(
            {
                "choices": [
                    {"message": {"content": payload}},
                ],
            },
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def stub_server() -> Generator[str, None, None]:
    _ResponseQueue.items = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    yield f"http://{host}:{port}/v1"
    server.shutdown()


def test_generate_brain_recommendation_validates_stub_response(stub_server: str) -> None:
    _ResponseQueue.items = [json.dumps(_valid_recommendation())]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    result = generate_brain_recommendation(
        client,
        [{"role": "user", "content": "recommend next step"}],
    )
    assert result["summary"] == "Run true."


def test_generate_brain_recommendation_retries_after_invalid_json(stub_server: str) -> None:
    _ResponseQueue.items = [
        "not-json",
        json.dumps(_valid_recommendation()),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    result = generate_brain_recommendation(
        client,
        [{"role": "user", "content": "recommend next step"}],
        max_retries=2,
    )
    assert result["recommendation_type"] == "action"


def test_generate_brain_recommendation_retries_after_schema_error(stub_server: str) -> None:
    invalid = _valid_recommendation()
    del invalid["risk"]
    _ResponseQueue.items = [
        json.dumps(invalid),
        json.dumps(_valid_recommendation()),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    result = generate_brain_recommendation(
        client,
        [{"role": "user", "content": "recommend next step"}],
        max_retries=2,
    )
    assert result["risk"]["level"] == "low"


def test_generate_brain_recommendation_raises_after_exhausted_retries(stub_server: str) -> None:
    _ResponseQueue.items = ["still-not-json", "still-not-json"]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    with pytest.raises(LLMError, match="failed to produce valid recommendation"):
        generate_brain_recommendation(
            client,
            [{"role": "user", "content": "recommend next step"}],
            max_retries=2,
        )
