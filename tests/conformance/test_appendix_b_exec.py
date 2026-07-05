"""Appendix B conformance vectors driven through wayfinder-exec."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import (
    create_goal_via_cli,
    event_types_from_history,
    issue_recommendation,
    owner_actor,
    run_cli,
    run_exec,
    run_update_via_cli,
    service_for_store,
    shell_action_recommendation,
    wait_for_event_type,
    write_exec_playbook,
    write_playbook,
)
from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import ArtifactIntegrityError
from wayfinder.exec.loop import ExecutorConfig, ExecutorLoop

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
    wait_for_event_type(store, goal_id, "action.started")
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert proc.returncode != 0

    resume = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert resume.returncode == 0, resume.stdout + resume.stderr

    event_types = event_types_from_history(store, goal_id)
    assert "action.blocked" in event_types
    assert event_types.count("action.started") == 1


def test_15_8_human_override_replacement(tmp_path: Path) -> None:
    """§15.8: executor validates and runs a human override replacement."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(
        {
            "schema": "wip.goal_create/0.1",
            "protocol_version": "0.1",
            "create_id": "create_override_01",
            "created_at": "2026-07-04T18:00:00Z",
            "actor": owner_actor(),
            "description": "Run tests.",
            "workspace_uri": f"file:{workspace}",
            "policy": {"max_auto_risk_level": "low"},
        },
    )
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    replacement = {
        "schema": "wip.recommendation/0.1",
        "protocol_version": "0.1",
        "goal_id": goal_id,
        "recommendation_id": "rec_pnpm_test",
        "recommendation_type": "action",
        "summary": "Run pnpm test instead.",
        "goal_status": "running",
        "confidence": 0.9,
        "executable": True,
        "issued_at": "2026-07-04T18:05:00Z",
        "expires_at": "2099-07-04T20:00:00Z",
        "parallel": False,
        "supersedes": [ctx["recommendation_id"]],
        "wayfinder": {"name": "human", "version": "0.1", "instance_id": "override"},
        "basis": {
            "event_log_seq": ctx["issued_event_seq"],
            "event_log_head": ctx["issued_event_hash"],
            "state_version": "override",
        },
        "lease": {
            "lease_id": "lease_pnpm",
            "lease_expires_at": "2099-07-04T20:00:00Z",
        },
        "action": {
            "action_id": "act_pnpm_test",
            "kind": "shell",
            "title": "Run pnpm test",
            "shell": {
                "argv": ["true"],
                "command_for_display": "pnpm test",
                "cwd": f"file:{workspace}",
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 60,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [],
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_pnpm_test",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 1,
        },
        "risk": {
            "level": "low",
            "classes": ["execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "run_id": None,
    }
    override = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_override_replace",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": owner_actor(),
            "update_type": "override",
            "override": {
                "decision": "replace",
                "reason": "prefer pnpm",
                "replacement_recommendation": replacement,
            },
        },
    )
    assert override.returncode == 0, override.stdout + override.stderr

    playbook = tmp_path / "done_after_replace.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(argv=["false"], workspace=workspace),
    )
    proc = run_exec(["run", "--goal-id", goal_id], store=store, playbook=playbook)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    events = [
        json.loads(line)
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    event_types = [event["type"] for event in events]
    assert "recommendation.issued" in event_types
    assert "recommendation.overridden" in event_types
    assert "action.started" in event_types
    assert "action.completed" in event_types

    started = next(event for event in events if event["type"] == "action.started")
    assert started["data"]["recommendation_id"] == "rec_pnpm_test"
    assert started["data"]["action_id"] == "act_pnpm_test"


def test_15_11_partial_artifact_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§15.11: failed artifact verification does not produce invalid references."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "large_output.json"
    write_exec_playbook(
        playbook,
        shell_action_recommendation(
            argv=[sys.executable, "-c", "print('x' * 12000)"],
            workspace=workspace,
        ),
    )
    created = create_goal_via_cli(store, workspace, playbook=playbook)
    goal_id = created["goal"]["goal_id"]

    def _fail_verify(_self: ArtifactStore, _artifact: dict[str, object]) -> None:
        msg = "simulated partial artifact write"
        raise ArtifactIntegrityError(msg)

    monkeypatch.setattr(ArtifactStore, "verify_reference", _fail_verify)

    loop = ExecutorLoop(
        ExecutorConfig(
            goal_id=goal_id,
            store=str(store),
            executor_id="exec_test",
            wayfinder_command=None,
            brain_playbook=str(playbook),
            policy_path=None,
            dry_run=False,
        ),
    )
    outcome = loop.run()
    assert outcome.stopped_reason == "goal_completed"

    events = [
        json.loads(line)
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    completed = next(event for event in events if event["type"] == "action.completed")
    action_result = completed["data"]["action_result"]
    artifacts = action_result.get("artifacts", [])
    assert artifacts == []
    assert "stdout_artifact" not in action_result.get("output", {})
    assert "stderr_artifact" not in action_result.get("output", {})
