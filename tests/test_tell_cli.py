"""wayfinder-tell CLI tests."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, issue_recommendation, service_for_store
from tests.conftest import StubResponseQueue
from wayfinder.core.errors import PolicyDeniedError
from wayfinder.exec.wayfinder_client import WayfinderClient
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig
from wayfinder.tell.main import run_tell


def test_run_tell_records_observation(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issue_recommendation(service, goal_id)
    StubResponseQueue.items = [
        json.dumps(
            {
                "update_type": "observation",
                "text": "FYI, CI is red on main for unrelated reasons.",
                "invalidates": False,
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    wayfinder_cmd = [sys.executable, "-m", "wayfinder.cli", "--brain", "scripted"]
    output = StringIO()
    result = run_tell(
        "FYI, CI is red on main for unrelated reasons.",
        goal_id,
        store=str(store),
        wayfinder_command=wayfinder_cmd,
        client=client,
        output_stream=output,
    )
    assert "observation" in str(result["receipt"])
    assert "recorded observation" in output.getvalue()


def test_run_tell_goal_cancel_denied_without_owner(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    StubResponseQueue.items = [
        json.dumps(
            {
                "update_type": "goal_cancel",
                "text": "requirements changed",
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    wayfinder_cmd = [sys.executable, "-m", "wayfinder.cli", "--brain", "scripted"]
    with pytest.raises(PolicyDeniedError, match="insufficient authority"):
        run_tell(
            "cancel this goal, requirements changed",
            goal_id,
            store=str(store),
            wayfinder_command=wayfinder_cmd,
            client=client,
            actor={"type": "human", "id": "guest", "authority": "operator"},
        )


def test_run_tell_submitted_update_visible_in_history(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issue_recommendation(service, goal_id)
    observation_text = "FYI, CI is red on main for unrelated reasons."
    StubResponseQueue.items = [
        json.dumps(
            {
                "update_type": "observation",
                "text": observation_text,
                "invalidates": False,
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    wayfinder_cmd = [sys.executable, "-m", "wayfinder.cli", "--brain", "scripted"]
    wf = WayfinderClient(command=wayfinder_cmd, store=str(store))
    before = len(wf.history(goal_id))
    run_tell(
        observation_text,
        goal_id,
        store=str(store),
        wayfinder_command=wayfinder_cmd,
        client=client,
        output_stream=StringIO(),
    )
    events = wf.history(goal_id)
    assert len(events) > before
    matching = [
        event
        for event in events
        if event.get("type") == "observation.recorded"
        and isinstance(event.get("data"), dict)
        and event["data"].get("observations")
        == [{"text": observation_text, "effective": {"invalidates": False}}]
    ]
    assert matching, "submitted observation must appear byte-visible in history"


def test_tell_main_json_format(
    stub_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfinder.tell.main import main

    monkeypatch.setenv("WAYFINDER_LLM_BASE_URL", stub_server)
    monkeypatch.setenv("WAYFINDER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WAYFINDER_LLM_MODEL", "test-model")
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
                "text": "note",
                "invalidates": False,
            },
        ),
    ]
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--format",
                "json",
                "--goal-id",
                goal_id,
                "--store",
                str(store),
                "note",
            ],
        )
    assert exc.value.code == 0
