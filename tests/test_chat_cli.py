"""wayfinder-chat CLI tests."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, service_for_store
from tests.conftest import StubResponseQueue
from wayfinder.chat.main import run_chat
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


def test_run_chat_ask_turn(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    StubResponseQueue.items = ["Status is pending [seq 1]."]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    output = StringIO()
    lines = iter(["what is the status?", ""])
    turns = run_chat(
        goal_id=goal_id,
        store=str(store),
        wayfinder_command=[sys.executable, "-m", "wayfinder.cli"],
        client=client,
        output_stream=output,
        read_line=lambda: next(lines, None),
    )
    assert len(turns) == 1
    assert turns[0]["kind"] == "ask"
    assert "chat:" in output.getvalue()


def test_run_chat_honors_quit(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    lines = iter(["quit"])
    turns = run_chat(
        goal_id=goal_id,
        store=str(store),
        wayfinder_command=[sys.executable, "-m", "wayfinder.cli"],
        client=client,
        read_line=lambda: next(lines, None),
    )
    assert turns == []


def test_run_chat_tell_turn(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    StubResponseQueue.items = [
        json.dumps(
            {
                "update_type": "observation",
                "text": "Manual note.",
                "invalidates": False,
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    lines = iter(["tell: manual note", ""])
    turns = run_chat(
        goal_id=goal_id,
        store=str(store),
        wayfinder_command=[sys.executable, "-m", "wayfinder.cli"],
        client=client,
        read_line=lambda: next(lines, None),
    )
    assert len(turns) == 1
    assert turns[0]["kind"] == "tell"


def test_chat_main_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wayfinder.chat.main import main

    monkeypatch.setattr(
        "wayfinder.chat.main.run_chat",
        lambda **_kwargs: [{"kind": "ask", "answer": "ok"}],
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--format",
                "json",
                "--goal-id",
                "goal_test",
                "--store",
                str(tmp_path),
            ],
        )
    assert exc.value.code == 0
