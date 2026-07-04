"""Appendix B conformance vectors exercised through the CLI subprocess."""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import pytest

from tests.conformance.helpers import (
    create_goal_via_cli,
    goal_create_payload,
    run_cli,
    service_for_store,
)

pytestmark = pytest.mark.conformance


def test_15_14_mandatory_cli_envelope(tmp_path: Path) -> None:
    """§15.14: successful commands use wip.response/0.1 envelope."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    proc = run_cli(["--store", str(store), "status", "--goal-id", goal_id, "--format=json"])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "wip.response/0.1"
    assert payload["result"]["schema"] == "wip.status/0.1"


def test_15_15_idempotent_goal_create(tmp_path: Path) -> None:
    """§15.15: identical create_id replays without a second goal.created."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    body = json.dumps(goal_create_payload(workspace, create_id="create_01"))
    first = run_cli(["--store", str(store), "goal", "create"], stdin=body)
    second = run_cli(["--store", str(store), "goal", "create"], stdin=body)
    assert first.returncode == 0
    assert second.returncode == 0
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert second_payload["result"]["replayed"] is True
    assert second_payload["result"]["goal"]["goal_id"] == first_payload["result"]["goal"]["goal_id"]
    assert (
        second_payload["result"]["events"][0]["seq"] == first_payload["result"]["events"][0]["seq"]
    )

    history = run_cli(
        [
            "--store",
            str(store),
            "history",
            "--goal-id",
            first_payload["result"]["goal"]["goal_id"],
            "--since-seq",
            "0",
        ],
    )
    assert len([line for line in history.stdout.splitlines() if line.strip()]) == 1


def test_15_16_conflicting_goal_create(tmp_path: Path) -> None:
    """§15.16: reused create_id with different content fails invalid_input."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    first = run_cli(
        ["--store", str(store), "goal", "create"],
        stdin=json.dumps(goal_create_payload(workspace, create_id="create_01")),
    )
    assert first.returncode == 0
    conflict = run_cli(
        ["--store", str(store), "goal", "create"],
        stdin=json.dumps(
            goal_create_payload(workspace, create_id="create_01", description="Different goal."),
        ),
    )
    assert conflict.returncode == 1
    error = json.loads(conflict.stdout)
    assert error["schema"] == "wip.error/0.1"
    assert error["error"]["code"] == "invalid_input"


def test_15_20_preview_is_not_explainable_later(tmp_path: Path) -> None:
    """§15.20: preview recommendations are not in durable history."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    preview_proc = run_cli(
        ["--store", str(store), "next", "--goal-id", goal_id, "--mode=preview"],
    )
    assert preview_proc.returncode == 0
    preview = json.loads(preview_proc.stdout)["result"]
    rec_id = preview["recommendation_id"]

    explain_proc = run_cli(
        [
            "--store",
            str(store),
            "explain",
            "--goal-id",
            goal_id,
            "--recommendation-id",
            rec_id,
        ],
    )
    assert explain_proc.returncode == 1
    error = json.loads(explain_proc.stdout)
    assert error["error"]["code"] == "invalid_input"


def test_15_28_supersession_is_explicit_and_atomic(tmp_path: Path) -> None:
    """§15.28: supersede atomically replaces the open recommendation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    service = service_for_store(store)

    issued = service.next(goal_id, mode="issue")
    rec_01 = issued["recommendation_id"]

    conflict = run_cli(
        ["--store", str(store), "next", "--goal-id", goal_id, "--mode=issue"],
    )
    assert conflict.returncode == 2
    assert json.loads(conflict.stdout)["error"]["code"] == "storage_conflict"

    history_before = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    assert len([line for line in history_before.stdout.splitlines() if line.strip()]) == 2

    superseded = service.next(goal_id, mode="issue", supersede=True)
    rec_02 = superseded["recommendation_id"]
    assert rec_02 != rec_01
    assert superseded["supersedes"] == [rec_01]

    history_after = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    events = [json.loads(line) for line in history_after.stdout.splitlines() if line.strip()]
    assert len(events) == 4
    assert events[2]["type"] == "recommendation.superseded"
    assert events[2]["data"]["recommendation_id"] == rec_01
    assert events[3]["type"] == "recommendation.issued"
    assert events[3]["data"]["recommendation"]["recommendation_id"] == rec_02

    status = json.loads(
        run_cli(["--store", str(store), "status", "--goal-id", goal_id]).stdout,
    )["result"]
    assert status["open_recommendation_id"] == rec_02


def test_15_31_goal_cancel(tmp_path: Path) -> None:
    """§15.31: owner cancel succeeds; operator is rejected; next fails."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    denied = run_cli(
        ["--store", str(store), "update", "--goal-id", goal_id],
        stdin=json.dumps(
            {
                "schema": "wip.update/0.1",
                "protocol_version": "0.1",
                "update_id": "upd_denied",
                "goal_id": goal_id,
                "created_at": "2026-07-04T18:05:00Z",
                "actor": {"type": "human", "id": "other", "authority": "operator"},
                "update_type": "goal_cancel",
                "goal_cancel": {"reason": "stop"},
            },
        ),
    )
    assert denied.returncode == 7
    assert json.loads(denied.stdout)["error"]["code"] == "policy_denied"

    cancel = run_cli(
        ["--store", str(store), "update", "--goal-id", goal_id],
        stdin=json.dumps(
            {
                "schema": "wip.update/0.1",
                "protocol_version": "0.1",
                "update_id": "upd_cancel",
                "goal_id": goal_id,
                "created_at": "2026-07-04T18:05:00Z",
                "actor": {"type": "human", "id": getpass.getuser(), "authority": "owner"},
                "update_type": "goal_cancel",
                "goal_cancel": {"reason": "no longer needed"},
            },
        ),
    )
    assert cancel.returncode == 0
    status = json.loads(cancel.stdout)["result"]["status"]
    assert status["goal_status"] == "cancelled"

    next_proc = run_cli(
        ["--store", str(store), "next", "--goal-id", goal_id, "--mode=issue"],
    )
    assert next_proc.returncode == 1
    assert json.loads(next_proc.stdout)["error"]["code"] == "invalid_input"


def test_15_32_idempotent_replay_returns_current_status(tmp_path: Path) -> None:
    """§15.32: replayed update returns original events and current status."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issued = service.next(goal_id, mode="issue")
    rec_id = str(issued["recommendation_id"])
    act_id = str(issued["action"]["action_id"])
    history_lines = service.history(goal_id, since_seq=0)
    issued_event = json.loads(history_lines[-1])

    accepted = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_accept",
        "goal_id": goal_id,
        "recommendation_id": rec_id,
        "action_id": act_id,
        "issued_event_seq": int(issued_event["seq"]),
        "issued_event_hash": str(issued_event["event_hash"]),
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {
            "type": "executor",
            "id": "exec-a",
            "authority": "operator",
            "authenticated": True,
        },
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "accepted"},
    }
    first = service.update(goal_id, accepted)
    assert first["replayed"] is False
    assert first["seq_start"] == 3

    observation = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_obs",
        "goal_id": goal_id,
        "created_at": "2026-07-04T18:06:00Z",
        "actor": {
            "type": "human",
            "id": getpass.getuser(),
            "authority": "owner",
            "authenticated": True,
        },
        "update_type": "observation",
        "observations": [{"text": "tests still running", "effective": {"invalidates": False}}],
    }
    service.update(goal_id, observation)

    replay = service.update(goal_id, accepted)
    assert replay["replayed"] is True
    assert replay["seq_start"] == replay["seq_end"] == 3
    assert replay["status"]["last_event_seq"] == 4


def test_15_37_non_action_payload_nesting(tmp_path: Path) -> None:
    """§15.37: blocked recommendations nest payload without action or lease."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "blocked_playbook.json"
    playbook.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "match": {
                            "goal_status": "pending",
                            "open_recommendation_id": {"$null": True},
                        },
                        "recommendation": {
                            "recommendation_type": "blocked",
                            "summary": "Blocked for conformance.",
                            "goal_status": "pending",
                            "confidence": 0.5,
                            "blocked": {
                                "reason_code": "test_blocked",
                                "reason": "Appendix B vector 15.37",
                            },
                        },
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    service = service_for_store(store, playbook=playbook)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issued = service.next(goal_id, mode="issue")
    assert issued["recommendation_type"] == "blocked"
    assert issued["executable"] is False
    assert "action" not in issued
    assert "lease" not in issued
    assert issued["blocked"]["reason_code"] == "test_blocked"


def test_15_38_reserved_run_id(tmp_path: Path) -> None:
    """§15.38: non-null run_id is rejected by schema validation."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    rejected = run_cli(
        ["--store", str(store), "update", "--goal-id", goal_id],
        stdin=json.dumps(
            {
                "schema": "wip.update/0.1",
                "protocol_version": "0.1",
                "update_id": "upd_run",
                "goal_id": goal_id,
                "created_at": "2026-07-04T18:05:00Z",
                "actor": {"type": "human", "id": getpass.getuser(), "authority": "owner"},
                "update_type": "goal_cancel",
                "goal_cancel": {"reason": "stop"},
                "run_id": "run_01",
            },
        ),
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["error"]["code"] == "invalid_input"

    accepted = run_cli(
        ["--store", str(store), "update", "--goal-id", goal_id],
        stdin=json.dumps(
            {
                "schema": "wip.update/0.1",
                "protocol_version": "0.1",
                "update_id": "upd_cancel_ok",
                "goal_id": goal_id,
                "created_at": "2026-07-04T18:05:00Z",
                "actor": {"type": "human", "id": getpass.getuser(), "authority": "owner"},
                "update_type": "goal_cancel",
                "goal_cancel": {"reason": "stop"},
                "run_id": None,
            },
        ),
    )
    assert accepted.returncode == 0
