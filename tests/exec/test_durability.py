"""Durability and interruption recovery tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import create_goal_via_cli, run_cli
from wayfinder.exec.durability import DurabilityStore, PendingAction


def _run_exec(args: list[str], *, store: Path, playbook: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "wayfinder.exec",
        "--store",
        str(store),
        "--brain-playbook",
        str(playbook),
        *args,
    ]
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def test_resume_submits_saved_action_result(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).parent / "fixtures" / "true_playbook.json"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    issued = run_cli(
        [
            "--store",
            str(store),
            "--brain-playbook",
            str(playbook),
            "next",
            "--goal-id",
            goal_id,
            "--mode=issue",
        ],
    )
    assert issued.returncode == 0
    recommendation = json.loads(issued.stdout)["result"]
    rec_id = recommendation["recommendation_id"]
    action_id = recommendation["action"]["action_id"]

    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    issued_event = json.loads(history.stdout.strip().splitlines()[-1])

    accept = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_accept_resume",
        "goal_id": goal_id,
        "recommendation_id": rec_id,
        "action_id": action_id,
        "issued_event_seq": issued_event["seq"],
        "issued_event_hash": issued_event["event_hash"],
        "created_at": "2026-07-04T18:03:00Z",
        "actor": {
            "type": "executor",
            "id": "wayfinder-exec-local",
            "authority": "operator",
            "authenticated": True,
        },
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "accepted"},
    }
    start = {
        **accept,
        "update_id": "upd_start_resume",
        "update_type": "action_started",
        "action_started": {"started_at": "2026-07-04T18:04:00Z"},
    }
    start.pop("recommendation_disposition", None)
    assert (
        run_cli(
            ["--store", str(store), "update", "--goal-id", goal_id],
            stdin=json.dumps(accept),
        ).returncode
        == 0
    )
    assert (
        run_cli(
            ["--store", str(store), "update", "--goal-id", goal_id],
            stdin=json.dumps(start),
        ).returncode
        == 0
    )

    durability = DurabilityStore(store, executor_id="wayfinder-exec-local")
    durability.save(
        PendingAction(
            goal_id=goal_id,
            recommendation_id=rec_id,
            action_id=action_id,
            issued_event_seq=int(issued_event["seq"]),
            issued_event_hash=str(issued_event["event_hash"]),
            accept_update_id="upd_accept_resume",
            start_update_id="upd_start_resume",
            result_update_id="upd_result_resume",
            stage="executed",
            action_result={
                "status": "completed",
                "changed": "no",
                "started_at": "2026-07-04T18:04:00Z",
                "ended_at": "2026-07-04T18:05:00Z",
                "process": {"exit_code": 0, "signal": None, "timed_out": False},
                "output": {"stdout": "", "stderr": ""},
            },
        ),
    )

    proc = _run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    history_after = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [
        json.loads(line)["type"] for line in history_after.stdout.splitlines() if line.strip()
    ]
    assert "action.completed" in event_types
    assert not (store / "executor-state" / "wayfinder-exec-local.json").exists()
