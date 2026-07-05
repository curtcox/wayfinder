"""wayfinder-web CLI integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, run_cli


def _run_web_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.web", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_web_cli_preview_first_scripted_step(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace)
    payload["metadata"] = {
        "web_steps": [
            {
                "title": "Open portal",
                "steps": [{"op": "navigate", "url": "https://vendor.example/billing"}],
            },
        ],
    }
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    preview = _run_web_cli(
        [
            "--store",
            str(store),
            "next",
            "--goal-id",
            goal_id,
            "--mode=preview",
            "--explain=structured",
        ],
    )
    assert preview.returncode == 0, preview.stdout + preview.stderr
    recommendation = json.loads(preview.stdout)["result"]
    assert recommendation["recommendation_type"] == "action"
    assert recommendation["executable"] is False
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["x_browser_steps"][0]["op"] == "navigate"
