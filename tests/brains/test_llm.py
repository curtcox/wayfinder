"""LLM brain tests with a local stub server."""

from __future__ import annotations

import json
from typing import Any

from tests.conftest import StubResponseQueue
from wayfinder.brains.llm import LLMBrain
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


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


def test_llm_brain_recommend_returns_valid_action(stub_server: str) -> None:
    StubResponseQueue.items = [json.dumps(_valid_recommendation())]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    brain = LLMBrain(client)
    recommendation = brain.recommend(
        goal={"goal_id": "goal_01", "description": "Run true."},
        status={"goal_status": "pending", "open_recommendation_id": None},
        events=[],
        mode="preview",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "action"
    assert recommendation["action"]["shell"]["argv"] == ["true"]
