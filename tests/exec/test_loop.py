"""Executor loop integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import create_goal_via_cli, goal_create_payload, run_cli


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


def test_exec_run_completes_goal(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).with_name("fixtures") / "true_playbook.json"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    proc = _run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "wip.response/0.1"
    assert payload["result"]["stopped_reason"] == "goal_completed"
    assert payload["result"]["status"]["goal_status"] == "succeeded"


def test_exec_dry_run_does_not_mutate_log(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).with_name("fixtures") / "true_playbook.json"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    before = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    proc = _run_exec(["dry-run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    assert before.stdout == after.stdout
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] == "dry_run"


def test_exec_denies_high_risk_action(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "risky.json"
    playbook.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "match": {"goal_status": "pending"},
                        "recommendation": {
                            "recommendation_type": "action",
                            "summary": "Delete everything.",
                            "goal_status": "running",
                            "confidence": 0.1,
                            "action": {
                                "kind": "shell",
                                "title": "rm",
                                "shell": {
                                    "argv": ["rm", "-rf", "."],
                                    "cwd": "file:{workspace_uri}",
                                    "env": {"mode": "minimal", "set": {}},
                                    "requires_shell": False,
                                },
                                "preconditions": [],
                                "success_criteria": [],
                            },
                            "idempotency": {
                                "level": "none",
                                "scope": "workspace",
                                "safe_to_retry": False,
                                "safe_to_run_if_already_done": False,
                                "dedupe_strategy": "none",
                            },
                            "risk": {
                                "level": "critical",
                                "classes": ["delete"],
                                "requires_approval": False,
                                "destructive": True,
                                "network": "not_required",
                                "secrets": "not_required",
                                "blast_radius": "host",
                            },
                        },
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    body = json.dumps(goal_create_payload(workspace))
    created = run_cli(
        ["--store", str(store), "--brain-playbook", str(playbook), "goal", "create"],
        stdin=body,
    )
    assert created.returncode == 0
    goal_id = json.loads(created.stdout)["result"]["goal"]["goal_id"]

    proc = _run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] == "policy_denied"
    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [json.loads(line)["type"] for line in history.stdout.splitlines() if line.strip()]
    assert "executor.policy_denied" in event_types
    assert "action.started" not in event_types
