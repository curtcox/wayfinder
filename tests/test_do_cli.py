"""wayfinder-do CLI tests."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from tests.conftest import StubResponseQueue
from wayfinder.do.main import run_do
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


def test_run_do_creates_goal_and_runs_scripted_brain(
    stub_server: str,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).parent / "exec" / "fixtures" / "true_playbook.json"
    StubResponseQueue.items = [
        json.dumps(
            {
                "description": "Run a no-op command.",
                "workspace_path": str(workspace),
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    wayfinder_cmd = [
        sys.executable,
        "-m",
        "wayfinder.cli",
        "--brain",
        "scripted",
        "--brain-playbook",
        str(playbook),
    ]
    output = StringIO()
    result = run_do(
        "Run a no-op command in this project.",
        store=str(store),
        wayfinder_command=wayfinder_cmd,
        client=client,
        output_stream=output,
    )
    assert result["stopped_reason"] == "goal_completed"
    status = result["status"]
    assert isinstance(status, dict)
    assert status["goal_status"] == "succeeded"
    lines = output.getvalue().splitlines()
    assert any("created" in line for line in lines)
    assert any("goal_" in line and "succeeded" in line for line in lines)


def test_do_main_json_format(
    stub_server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wayfinder.do.main import main

    monkeypatch.setenv("WAYFINDER_LLM_BASE_URL", stub_server)
    monkeypatch.setenv("WAYFINDER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WAYFINDER_LLM_MODEL", "test-model")
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).parent / "exec" / "fixtures" / "true_playbook.json"
    StubResponseQueue.items = [
        json.dumps(
            {
                "description": "Run a no-op command.",
                "workspace_path": str(workspace),
            },
        ),
    ]
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--format",
                "json",
                "--store",
                str(store),
                "--wayfinder",
                f"{sys.executable} -m wayfinder.cli --brain scripted --brain-playbook {playbook}",
                "Run a no-op.",
            ],
        )
    assert exc.value.code == 0
