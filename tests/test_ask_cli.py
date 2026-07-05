"""wayfinder-ask CLI tests."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, service_for_store
from tests.conftest import StubResponseQueue
from wayfinder.ask.main import run_ask
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
