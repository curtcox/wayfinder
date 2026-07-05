"""wayfinder-ask CLI tests."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, service_for_store
from tests.conftest import StubResponseQueue
from wayfinder.ask.main import run_ask
from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


def test_run_ask_synthesizes_answer(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    StubResponseQueue.items = ["The goal is pending with no open recommendation [seq 1]."]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    wayfinder_cmd = [sys.executable, "-m", "wayfinder.cli", "--brain", "scripted"]
    output = StringIO()
    result = run_ask(
        "why is this stuck?",
        goal_id=goal_id,
        store=str(store),
        wayfinder_command=wayfinder_cmd,
        client=client,
        output_stream=output,
    )
    assert "pending" in str(result["answer"]).lower()
    assert "pending" in output.getvalue().lower()


def test_run_ask_store_wide(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    service.goal_create(goal_create_payload(workspace))
    StubResponseQueue.items = ["One goal is pending."]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    output = StringIO()
    result = run_ask(
        "which goals are waiting on me?",
        store=str(store),
        wayfinder_command=[sys.executable, "-m", "wayfinder.cli"],
        client=client,
        output_stream=output,
    )
    assert "One goal" in str(result["answer"])


def test_run_ask_recommendation_requires_goal_id(stub_server: str) -> None:
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    with pytest.raises(InvalidInputError, match="requires --goal-id"):
        run_ask("review this", recommendation_id="rec_test", client=client)


def test_ask_main_json_format(
    stub_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfinder.ask.main import main

    monkeypatch.setenv("WAYFINDER_LLM_BASE_URL", stub_server)
    monkeypatch.setenv("WAYFINDER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WAYFINDER_LLM_MODEL", "test-model")
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    StubResponseQueue.items = ["Pending [seq 1]."]
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--format",
                "json",
                "--goal-id",
                goal_id,
                "--store",
                str(store),
                "why",
                "stuck?",
            ],
        )
    assert exc.value.code == 0
