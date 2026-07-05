"""wayfinder-exec-temporal CLI integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, run_cli


def _run_exec_temporal(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.exec_temporal", *args]
    env = {**os.environ, "WAYFINDER_TEMPORAL_STUB": "1"}
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_exec_temporal_stub_dry_run(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace)
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    dry_run = _run_exec_temporal(
        [
            "--store",
            str(store),
            "dry-run",
            "--goal-id",
            goal_id,
        ],
    )
    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    body = json.loads(dry_run.stdout)
    assert body["result"]["extensions"]["temporal"] is True
    assert body["result"]["extensions"]["stub"] is True
    assert body["result"]["recommendation"]["recommendation_type"] == "action"
