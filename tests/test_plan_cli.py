"""wayfinder-plan CLI integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, run_cli

_DOMAIN = Path(__file__).resolve().parents[1] / "examples" / "domains" / "cluster-maintenance.pddl"

_PROBLEM = """(define (problem upgrade-node3)
  (:domain cluster-maintenance)
  (:objects n3 n4 n5 - node)
  (:init (serving n3) (serving n4) (serving n5))
  (:goal (and (upgraded n3) (serving n3)))
)"""


def _run_plan_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.plan", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_cli_preview_first_step(tmp_path: Path) -> None:
    pytest.importorskip("pyperplan")
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace)
    payload["metadata"] = {
        "pddl_problem": _PROBLEM,
        "plan_actions": {
            "drain n3": {"argv": ["echo", "drain", "n3"], "title": "Drain node3"},
            "upgrade n3": {"argv": ["echo", "upgrade", "n3"], "title": "Upgrade node3"},
            "bring-online n3": {"argv": ["echo", "online", "n3"], "title": "Bring node3 online"},
        },
    }
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    preview = _run_plan_cli(
        [
            "--domain",
            str(_DOMAIN),
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
