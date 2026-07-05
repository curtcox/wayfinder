"""wayfinder-exec CLI tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wayfinder.exec.main import main

from tests.conformance.helpers import create_goal_via_cli


def test_main_requires_goal_selector() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run"])
    assert exc_info.value.code == 1


def test_run_accepts_goal_file(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).parent / "exec" / "fixtures" / "true_playbook.json"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    goal_file = tmp_path / "goal_id.txt"
    goal_file.write_text(goal_id, encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "wayfinder.exec",
        "--store",
        str(store),
        "--brain-playbook",
        str(playbook),
        "run",
        "--goal-file",
        str(goal_file),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["result"]["goal_id"] == goal_id
