"""Update mapping and goal store integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wayfinder.core.errors import PolicyDeniedError
from wayfinder.core.goal_store import GoalStore
from wayfinder.core.hash_chain import with_event_hash
from wayfinder.core.updates import map_update_to_events

_EVENT_COUNTER = 0


def _next_event_id() -> str:
    global _EVENT_COUNTER  # noqa: PLW0603
    _EVENT_COUNTER += 1
    return f"evt_{_EVENT_COUNTER:08d}"


def _seed_goal(store: GoalStore) -> dict[str, object]:
    goal_event = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_seed",
        "type": "goal.created",
        "time": "2026-07-04T18:00:00Z",
        "goal_id": store.goal_id,
        "source": "wayfinder://test",
        "actor": {"type": "human", "id": "curt", "authority": "owner", "authenticated": True},
        "data": {
            "goal": {
                "schema": "wip.goal/0.1",
                "protocol_version": "0.1",
                "goal_id": store.goal_id,
                "goal_status": "pending",
            },
        },
    }
    return store.append_events([goal_event], holder="test").events[0]


def _issue_action(store: GoalStore) -> tuple[dict[str, object], dict[str, object]]:
    recommendation = {
        "recommendation_id": "rec_01",
        "executable": True,
        "goal_status": "running",
        "recommendation_type": "action",
        "expires_at": "2099-07-04T20:00:00Z",
        "lease": {"lease_expires_at": "2099-07-04T20:00:00Z"},
        "action": {"action_id": "act_01"},
    }
    issued = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_issue",
        "type": "recommendation.issued",
        "time": "2026-07-04T18:01:00Z",
        "goal_id": store.goal_id,
        "source": "wayfinder://test",
        "actor": {"type": "wayfinder", "id": "wf", "authority": "operator"},
        "data": {"recommendation": recommendation},
    }
    event = store.append_events([issued], holder="test").events[0]
    return event, recommendation


def test_goal_cancel_requires_owner(tmp_path: Path) -> None:
    store = GoalStore(tmp_path, "goal_01")
    _seed_goal(store)
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_cancel",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:02:00Z",
        "actor": {"type": "human", "id": "guest", "authority": "operator", "authenticated": True},
        "update_type": "goal_cancel",
        "goal_cancel": {"reason": "stop"},
    }
    with pytest.raises(PolicyDeniedError):
        map_update_to_events(
            update,
            events=store.read_events(),
            recommendation=None,
            event_id_factory=_next_event_id,
        )


def test_apply_update_idempotent(tmp_path: Path) -> None:
    store = GoalStore(tmp_path, "goal_01")
    _seed_goal(store)
    _, _recommendation = _issue_action(store)
    events = store.read_events()
    issue_event = events[-1]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_01",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "action_id": "act_01",
        "issued_event_seq": issue_event["seq"],
        "issued_event_hash": issue_event["event_hash"],
        "created_at": "2026-07-04T18:03:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "action_started",
        "action_started": {"started_at": "2026-07-04T18:03:00Z"},
    }
    first = store.apply_update(
        update,
        holder="exec",
        event_id_factory=_next_event_id,
        now=datetime(2026, 7, 4, 18, 3, tzinfo=UTC),
    )
    second = store.apply_update(
        update,
        holder="exec",
        event_id_factory=_next_event_id,
        now=datetime(2026, 7, 4, 18, 3, tzinfo=UTC),
    )
    assert first.seq_start == second.seq_start
    assert len(store.read_events()) == 3


def test_map_action_result_to_failed_event() -> None:
    recommendation = {
        "recommendation_id": "rec_01",
        "executable": True,
        "expires_at": "2099-07-04T20:00:00Z",
        "lease": {"lease_expires_at": "2099-07-04T20:00:00Z"},
        "action": {"action_id": "act_01"},
    }
    issued = with_event_hash(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": "evt_issue",
            "type": "recommendation.issued",
            "time": "2026-07-04T18:01:00Z",
            "goal_id": "goal_01",
            "seq": 2,
            "source": "wayfinder://test",
            "actor": {"type": "wayfinder", "id": "wf", "authority": "operator"},
            "data": {"recommendation": recommendation},
        },
        prev_event_hash="sha256:" + "a" * 64,
    )
    started = with_event_hash(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": "evt_start",
            "type": "action.started",
            "time": "2026-07-04T18:02:00Z",
            "goal_id": "goal_01",
            "seq": 3,
            "source": "executor://exec",
            "actor": {"type": "executor", "id": "exec", "authority": "operator"},
            "invalidates_open_recommendations": False,
            "data": {
                "recommendation_id": "rec_01",
                "action_id": "act_01",
                "started_at": "2026-07-04T18:02:00Z",
            },
        },
        prev_event_hash=str(issued["event_hash"]),
    )
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_result",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "action_id": "act_01",
        "issued_event_seq": 2,
        "issued_event_hash": issued["event_hash"],
        "created_at": "2026-07-04T18:04:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "action_result",
        "action_result": {
            "status": "failed",
            "changed": "no",
            "started_at": "2026-07-04T18:02:00Z",
            "ended_at": "2026-07-04T18:04:00Z",
            "process": {"exit_code": 1, "signal": None, "timed_out": False},
        },
    }
    mapped = map_update_to_events(
        update,
        events=[issued, started],
        recommendation=recommendation,
        event_id_factory=_next_event_id,
        now=datetime(2026, 7, 4, 18, 4, tzinfo=UTC),
    )
    assert mapped[0]["type"] == "action.failed"
