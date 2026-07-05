"""wayfinder-tell CLI tests."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, issue_recommendation, service_for_store
from tests.conftest import StubResponseQueue
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
