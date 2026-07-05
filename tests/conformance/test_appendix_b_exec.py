"""Appendix B conformance vectors driven through wayfinder-exec."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conformance.helpers import (
    create_goal_via_cli,
    event_types_from_history,
    run_cli,
    run_exec,
    shell_action_recommendation,
    write_exec_playbook,
    write_playbook,
)

pytestmark = pytest.mark.conformance


def test_15_1_successful_shell_action(tmp_path: Path) -> None:
    """§15.1: executor accepts, runs, and reports a successful shell action."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "true.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(argv=["true"], workspace=workspace),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] == "goal_completed"

    event_types = event_types_from_history(store, goal_id)
    assert "recommendation.issued" in event_types
    assert "recommendation.accepted" in event_types
    assert "action.started" in event_types
    assert "action.completed" in event_types

    verify = run_cli(["--store", str(store), "verify", "--goal-id", goal_id])
    assert verify.returncode == 0
    assert json.loads(verify.stdout)["result"]["ok"] is True


def test_15_2_failed_shell_action(tmp_path: Path) -> None:
    """§15.2: non-zero exit is reported as action.failed."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "false.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(argv=["false"], workspace=workspace),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "action.failed" in event_types
    assert "action.started" in event_types


def test_15_3_unsupported_action_kind(tmp_path: Path) -> None:
    """§15.3: unknown action kinds are rejected without execution."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "http.json"
    recommendation = shell_action_recommendation(argv=["true"], workspace=workspace, kind="http")
    recommendation["action"].pop("shell", None)
    write_playbook(playbook, recommendation)
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "recommendation.rejected" in event_types
    assert "action.started" not in event_types


def test_15_4_unsupported_precondition(tmp_path: Path) -> None:
    """§15.4: unsupported preconditions produce action.blocked."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "custom_pre.json"
    write_playbook(
        playbook,
        shell_action_recommendation(
            argv=["true"],
            workspace=workspace,
            preconditions=[{"kind": "custom"}],
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "action.blocked" in event_types
    assert "action.started" not in event_types


def test_15_6_duplicate_executor_attempt(tmp_path: Path) -> None:
    """§15.6: executor run performs an external action at most once per recommendation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    marker = workspace / "ran_once"
    playbook = tmp_path / "marker.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(
            argv=["touch", str(marker)],
            workspace=workspace,
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    first = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert first.returncode == 0, first.stdout + first.stderr
    assert marker.exists()

    before = event_types_from_history(store, goal_id)
    started_count = before.count("action.started")
    completed_count = before.count("action.completed")
    assert started_count == 1
    assert completed_count == 1

    second = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert second.returncode == 0, second.stdout + second.stderr
    after = event_types_from_history(store, goal_id)
    assert after.count("action.started") == started_count
    assert after.count("action.completed") == completed_count


def test_15_9_policy_denied_destructive_action(tmp_path: Path) -> None:
    """§15.9: destructive argv is denied under default policy."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "rm.json"
    recommendation = shell_action_recommendation(
        argv=["rm", "-rf", "build"],
        workspace=workspace,
    )
    recommendation["risk"] = {
        "level": "high",
        "classes": ["delete"],
        "blast_radius": "workspace",
        "requires_approval": False,
        "destructive": True,
        "network": "not_required",
        "secrets": "not_required",
        "rollback": {"available": False, "kind": "unknown", "instructions": None},
    }
    write_playbook(playbook, recommendation)
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] == "policy_denied"

    event_types = event_types_from_history(store, goal_id)
    assert "executor.policy_denied" in event_types
    assert "action.started" not in event_types


def test_15_10_timeout(tmp_path: Path) -> None:
    """§15.10: timed-out commands are killed and recorded."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "sleep.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(
            argv=["sleep", "60"],
            workspace=workspace,
            timeout_seconds=1,
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "action.timed_out" in event_types
    assert "action.started" in event_types


def test_15_19_secret_environment_value_rejected(tmp_path: Path) -> None:
    """§15.19: plaintext sensitive env values are rejected before spawn."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "secret_env.json"
    write_playbook(
        playbook,
        shell_action_recommendation(
            argv=["true"],
            workspace=workspace,
            env_set={"API_KEY": {"value": "secret", "sensitive": True}},
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] == "policy_denied"

    event_types = event_types_from_history(store, goal_id)
    assert "executor.policy_denied" in event_types
    assert "action.started" not in event_types


def test_exec_sigkill_mid_action_resumes_without_reexecution(tmp_path: Path) -> None:
    """Interrupted executor resumes with blocked result instead of re-running."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "sleep.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(
            argv=["sleep", "120"],
            workspace=workspace,
            timeout_seconds=300,
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    cmd = [
        sys.executable,
        "-m",
        "wayfinder.exec",
        "--store",
        str(store),
        "--brain-playbook",
        str(playbook),
        "run",
        "--goal-id",
        goal_id,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(2)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert proc.returncode != 0

    resume = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert resume.returncode == 0, resume.stdout + resume.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "action.blocked" in event_types
    assert event_types.count("action.started") == 1
