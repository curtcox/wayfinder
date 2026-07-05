"""Appendix B conformance vectors for update lifecycle and freshness."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import (
    append_observations,
    corrupt_event_hash_at,
    create_goal_via_cli,
    executor_actor,
    goal_create_payload,
    issue_recommendation,
    owner_actor,
    run_cli,
    run_update_via_cli,
    seed_expired_recommendation,
    service_for_store,
)
from wayfinder.core.goal_store import GoalStore

pytestmark = pytest.mark.conformance


def test_15_5_stale_recommendation(tmp_path: Path) -> None:
    """§15.5: invalidating correction makes action_started stale."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)

    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_correction",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:04:00Z",
            "actor": owner_actor(),
            "update_type": "correction",
            "correction": {
                "text": "requirements changed",
                "effective": {"invalidates": True},
            },
        },
    )

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start_stale",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:05:00Z"},
        },
    )
    assert proc.returncode == 5
    error = json.loads(proc.stdout)
    assert error["error"]["code"] == "stale_recommendation"

    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [json.loads(line)["type"] for line in history.stdout.splitlines() if line.strip()]
    assert "action.started" not in event_types


def test_15_7_concurrent_next_issue(tmp_path: Path) -> None:
    """§15.7: concurrent issue calls produce one recommendation and one conflict."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    cmd = [
        sys.executable,
        "-m",
        "wayfinder.cli",
        "--store",
        str(store),
        "next",
        "--goal-id",
        goal_id,
        "--mode=issue",
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(subprocess.run, cmd, capture_output=True, text=True, check=False)
        second = pool.submit(subprocess.run, cmd, capture_output=True, text=True, check=False)
        results = [first.result(), second.result()]

    codes = sorted(proc.returncode for proc in results)
    assert codes == [0, 2]
    payloads = [json.loads(proc.stdout) for proc in results]
    successes = [payload for payload in payloads if payload.get("schema") == "wip.response/0.1"]
    failures = [payload for payload in payloads if payload.get("schema") == "wip.error/0.1"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0]["error"]["code"] == "storage_conflict"

    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    issued = [
        json.loads(line)
        for line in history.stdout.splitlines()
        if line.strip() and json.loads(line)["type"] == "recommendation.issued"
    ]
    assert len(issued) == 1


def test_15_12_corrupted_event_log(tmp_path: Path) -> None:
    """§15.12: corrupted log is rejected by status and verify."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    issue_recommendation(service_for_store(store), goal_id)
    corrupt_event_hash_at(store, goal_id, seq=2)

    status = run_cli(["--store", str(store), "status", "--goal-id", goal_id])
    assert status.returncode == 8
    assert json.loads(status.stdout)["error"]["code"] == "corrupt_event_log"

    verify = run_cli(["--store", str(store), "verify", "--goal-id", goal_id])
    assert verify.returncode == 0
    result = json.loads(verify.stdout)["result"]
    assert result["ok"] is False
    assert any(problem["kind"] == "hash_mismatch" for problem in result["problems"])


def test_15_17_same_action_lifecycle_not_stale(tmp_path: Path) -> None:
    """§15.17: terminal result accepted after accepted/started lifecycle events."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)

    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:03:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:04:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:04:00Z"},
        },
    )

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_complete",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_result",
            "action_result": {
                "status": "completed",
                "process": {"exit_code": 0, "timed_out": False},
            },
        },
    )
    assert proc.returncode == 0
    events = [
        json.loads(line)["type"]
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    assert "action.completed" in events


def test_15_18_duplicate_terminal_action_event(tmp_path: Path) -> None:
    """§15.18: second terminal result for same action is rejected."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    terminal = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_terminal_1",
        "goal_id": goal_id,
        "recommendation_id": ctx["recommendation_id"],
        "action_id": ctx["action_id"],
        "issued_event_seq": ctx["issued_event_seq"],
        "issued_event_hash": ctx["issued_event_hash"],
        "created_at": "2026-07-04T18:05:00Z",
        "actor": executor_actor("exec-a"),
        "update_type": "action_result",
        "action_result": {
            "status": "completed",
            "process": {"exit_code": 0, "timed_out": False},
        },
    }
    assert service.update(goal_id, terminal)["replayed"] is False

    duplicate = {**terminal, "update_id": "upd_terminal_2"}
    proc = run_update_via_cli(store, goal_id, duplicate)
    assert proc.returncode in {1, 5}
    error = json.loads(proc.stdout)
    assert error["error"]["code"] in {"invalid_input", "stale_recommendation"}


def test_15_21_replacement_override_materializes_recommendation(tmp_path: Path) -> None:
    """§15.21: replace override embeds a full executable replacement recommendation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    replacement = {
        "schema": "wip.recommendation/0.1",
        "protocol_version": "0.1",
        "goal_id": goal_id,
        "recommendation_id": "rec_replacement",
        "recommendation_type": "action",
        "summary": "Run pnpm test instead.",
        "goal_status": "running",
        "confidence": 0.9,
        "executable": True,
        "issued_at": "2026-07-04T18:05:00Z",
        "expires_at": "2099-07-04T20:00:00Z",
        "parallel": False,
        "supersedes": [],
        "wayfinder": {"name": "human", "version": "0.1", "instance_id": "override"},
        "basis": {
            "event_log_seq": ctx["issued_event_seq"],
            "event_log_head": ctx["issued_event_hash"],
            "state_version": "override",
        },
        "lease": {
            "lease_id": "lease_replacement",
            "lease_expires_at": "2099-07-04T20:00:00Z",
        },
        "action": {
            "action_id": "act_replacement",
            "kind": "shell",
            "title": "Run pnpm test",
            "shell": {
                "argv": ["pnpm", "test"],
                "command_for_display": "pnpm test",
                "cwd": f"file:{workspace}",
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 600,
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
            "max_attempts": 2,
        },
        "risk": {
            "level": "low",
            "classes": ["read_local", "execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "run_id": None,
    }

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_replace",
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
    assert proc.returncode == 0
    events = [
        json.loads(line)
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    overridden = next(event for event in events if event["type"] == "recommendation.overridden")
    embedded = overridden["data"]["replacement_recommendation"]
    assert embedded["recommendation_id"] == "rec_replacement"
    assert embedded["action"]["shell"]["argv"] == ["pnpm", "test"]
    assert embedded["lease"]["lease_id"] == "lease_replacement"


def test_15_23_accepting_done_completes_goal(tmp_path: Path) -> None:
    """§15.23: accepting a done recommendation completes the goal."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    action_ctx = issue_recommendation(service, goal_id)
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_complete_action",
            "goal_id": goal_id,
            "recommendation_id": action_ctx["recommendation_id"],
            "action_id": action_ctx["action_id"],
            "issued_event_seq": action_ctx["issued_event_seq"],
            "issued_event_hash": action_ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_result",
            "action_result": {
                "status": "completed",
                "process": {"exit_code": 0, "timed_out": False},
            },
        },
    )

    done = service.next(goal_id, mode="issue")
    assert done["recommendation_type"] == "done"
    history_lines = service.history(goal_id, since_seq=0)
    issued_event = json.loads(history_lines[-1])
    done_ctx = {
        "recommendation_id": str(done["recommendation_id"]),
        "issued_event_seq": int(issued_event["seq"]),
        "issued_event_hash": str(issued_event["event_hash"]),
    }

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept_done",
            "goal_id": goal_id,
            "recommendation_id": done_ctx["recommendation_id"],
            "issued_event_seq": done_ctx["issued_event_seq"],
            "issued_event_hash": done_ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:06:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )
    assert proc.returncode == 0
    status = json.loads(proc.stdout)["result"]["status"]
    assert status["goal_status"] == "succeeded"
    assert status["open_recommendation_id"] is None


def test_15_24_fresh_immediately_after_issuance(tmp_path: Path) -> None:
    """§15.24: recommendation stays fresh when only issuance follows basis."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)

    accept = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:03:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )
    assert accept.returncode == 0

    start = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:04:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:04:00Z"},
        },
    )
    assert start.returncode == 0


def test_15_25_defaulted_flag_counts_for_freshness(tmp_path: Path) -> None:
    """§15.25: heartbeat without invalidates flag does not stale recommendations."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_heartbeat",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:03:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "heartbeat",
            "heartbeat": {"status": "alive"},
        },
    )

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept_after_heartbeat",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:04:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )
    assert proc.returncode == 0


def test_15_26_terminal_result_after_post_start_invalidation(tmp_path: Path) -> None:
    """§15.26: started action can still submit terminal result after invalidation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:03:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:04:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:04:00Z"},
        },
    )
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_obs",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:04:30Z",
            "actor": owner_actor(),
            "update_type": "observation",
            "observations": [{"text": "context changed", "effective": {"invalidates": True}}],
        },
    )

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_terminal",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_result",
            "action_result": {
                "status": "completed",
                "process": {"exit_code": 0, "timed_out": False},
            },
        },
    )
    assert proc.returncode == 0


def test_15_27_claimed_lease_blocks_second_executor(tmp_path: Path) -> None:
    """§15.27: second executor cannot start after another claims the recommendation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_accept_a",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:03:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "accepted"},
        },
    )

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start_b",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:04:00Z",
            "actor": executor_actor("exec-b"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:04:00Z"},
        },
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["code"] == "storage_conflict"


def test_15_29_expiry_is_event_driven(tmp_path: Path) -> None:
    """§15.29: reducer ignores wall clock; expiry requires an explicit event."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = seed_expired_recommendation(service, goal_id, workspace)

    first = service.status(goal_id)
    second = service.status(goal_id)
    assert first["open_recommendation_id"] == ctx["recommendation_id"]
    assert second["open_recommendation_id"] == ctx["recommendation_id"]
    assert first["goal_status"] == second["goal_status"]

    stale_start = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_start_expired",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "action_id": ctx["action_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "action_started",
            "action_started": {"started_at": "2026-07-04T18:05:00Z"},
        },
    )
    assert stale_start.returncode == 5

    expired = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_expire",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "issued_event_seq": ctx["issued_event_seq"],
            "issued_event_hash": ctx["issued_event_hash"],
            "created_at": "2026-07-04T18:06:00Z",
            "actor": executor_actor("exec-a"),
            "update_type": "recommendation_disposition",
            "recommendation_disposition": {"disposition": "expired"},
        },
    )
    assert expired.returncode == 0
    status = json.loads(expired.stdout)["result"]["status"]
    assert status["open_recommendation_id"] is None


def test_15_30_override_mark_done_completes_goal(tmp_path: Path) -> None:
    """§15.30: owner mark_done override completes the goal atomically."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    ctx = issue_recommendation(service, goal_id)

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_mark_done",
            "goal_id": goal_id,
            "recommendation_id": ctx["recommendation_id"],
            "created_at": "2026-07-04T18:05:00Z",
            "actor": owner_actor(),
            "update_type": "override",
            "override": {"decision": "mark_done", "reason": "good enough"},
        },
    )
    assert proc.returncode == 0
    status = json.loads(proc.stdout)["result"]["status"]
    assert status["goal_status"] == "succeeded"
    events = [
        json.loads(line)["type"]
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    assert events[-2:] == ["recommendation.overridden", "goal.completed"]


def test_15_33_cross_implementation_lock_exclusion(tmp_path: Path) -> None:
    """§15.33: independent lock probe fails while the CLI holds append.lock."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    goal_store = GoalStore(store, goal_id)
    probe = Path(__file__).resolve().parent / "scripts" / "lock_probe.py"

    with goal_store.lock.acquire("conformance-holder"):
        proc = subprocess.run(
            [
                sys.executable,
                str(probe),
                "--store",
                str(store),
                "--goal-id",
                goal_id,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    assert proc.returncode == 2


def test_15_35_history_failure_mid_stream(tmp_path: Path) -> None:
    """§15.35: history streams valid prefix then exits nonzero on corruption."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    append_observations(service, goal_id, count=98)
    corrupt_event_hash_at(store, goal_id, seq=50)

    proc = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    assert proc.returncode == 8
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) >= 50
    valid_events = [json.loads(line) for line in lines[:-1]]
    assert all(event["seq"] <= 49 for event in valid_events)
    error = json.loads(lines[-1])
    assert error["schema"] == "wip.error/0.1"
    assert error["error"]["code"] == "corrupt_event_log"


def test_15_36_redacted_artifact_replacement(tmp_path: Path) -> None:
    """§15.36: redaction update records replacement artifact metadata."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    goal_store = GoalStore(store, goal_id)
    secret = b"token=super-secret-value"
    original = goal_store.artifacts.write_bytes(secret, artifact_id="art_01")
    replacement = goal_store.artifacts.write_bytes(b"[REDACTED]", artifact_id="art_01_redacted")

    proc = run_update_via_cli(
        store,
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_redact",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:05:00Z",
            "actor": owner_actor(),
            "update_type": "redaction",
            "redaction": {
                "target_artifact_id": "art_01",
                "reason": "secret leaked in stdout",
                "replacement_artifact": replacement,
            },
        },
    )
    assert proc.returncode == 0
    events = [
        json.loads(line)
        for line in run_cli(
            ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
        ).stdout.splitlines()
        if line.strip()
    ]
    redaction = next(event for event in events if event["type"] == "redaction.recorded")
    recorded = redaction["data"]["redaction"]["replacement_artifact"]
    assert recorded["artifact_id"] == "art_01_redacted"


def test_15_13_replay_from_snapshot(tmp_path: Path) -> None:
    """§15.13: snapshot replay reconstructs the same status as full replay."""
    from wayfinder.core.reducer import reduce_events
    from wayfinder.core.snapshot import reduce_from_snapshot, write_snapshot

    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    append_observations(service, goal_id, count=54)

    goal_store = GoalStore(store, goal_id)
    events = goal_store.read_events()
    assert len(events) >= 55

    snapshot = write_snapshot(store, goal_id, events, seq=50)
    full_state = reduce_events(events)
    snapshot_state = reduce_from_snapshot(snapshot, events)

    assert snapshot_state.goal_status == full_state.goal_status
    assert snapshot_state.completed_steps == full_state.completed_steps
    assert snapshot_state.last_event_seq == full_state.last_event_seq
    assert snapshot_state.event_log_head == full_state.event_log_head

    status_from_store = goal_store.status(observed_at="2026-07-04T19:00:00Z")
    status_full = full_state.to_status(observed_at="2026-07-04T19:00:00Z")
    assert status_from_store == status_full
