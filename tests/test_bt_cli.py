"""wayfinder-bt CLI integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, run_cli


def _run_bt_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.bt", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bt_cli_preview_wait_recommendation(tmp_path: Path) -> None:
    pytest.importorskip("py_trees")
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    tree = Path(__file__).resolve().parents[1] / "examples" / "trees" / "test-check.bt"
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(goal_create_payload(workspace)),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    preview = _run_bt_cli(
        [
            "--tree",
            str(tree),
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
