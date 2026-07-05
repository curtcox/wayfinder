"""wayfinder-tw CLI integration tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, run_cli


def _run_tw_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.tw", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture(scope="module")
def task_binary() -> str:
    found = shutil.which("task")
    if found is None:
        pytest.skip("task (Taskwarrior) is not installed")
    return found


def test_tw_cli_preview_shows_highest_urgency_task(tmp_path: Path, task_binary: str) -> None:
    del task_binary
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    metadata = {
        "tw_tasks": [
            {"description": "export access logs", "argv": ["echo", "export"]},
            {
                "description": "run audit script",
                "depends_on": [0],
                "argv": ["echo", "audit"],
                "priority": "H",
            },
        ],
    }
    payload = goal_create_payload(workspace, description="Compliance evidence.")
    payload["metadata"] = metadata
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    preview = _run_tw_cli(
        [
            "--store",
            str(store),
            "next",
            "--goal-id",
            goal_id,
            "--mode=preview",
            "--explain=summary",
        ],
    )
    assert preview.returncode == 0, preview.stdout + preview.stderr
    recommendation = json.loads(preview.stdout)["result"]
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["argv"] == ["echo", "export"]
