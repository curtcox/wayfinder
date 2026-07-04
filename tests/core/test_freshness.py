"""Freshness and claim evaluation tests."""

from __future__ import annotations

from datetime import UTC, datetime

from wayfinder.core.freshness import evaluate_executable, is_fresh
from wayfinder.core.hash_chain import with_event_hash


def _issued(rec_id: str = "rec_01") -> dict[str, object]:
    return {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_2",
        "type": "recommendation.issued",
        "time": "2026-07-04T18:01:00Z",
        "goal_id": "goal_01",
        "source": "wayfinder://test",
        "actor": {"type": "wayfinder", "id": "wf", "authority": "operator"},
        "data": {
            "recommendation": {
                "recommendation_id": rec_id,
                "executable": True,
                "expires_at": "2026-07-04T19:00:00Z",
                "lease": {"lease_expires_at": "2026-07-04T19:00:00Z"},
                "action": {"action_id": "act_01"},
            },
        },
    }


def _stamp(events: list[dict[str, object]]) -> list[dict[str, object]]:
    stamped: list[dict[str, object]] = []
    prev = None
    for index, event in enumerate(events, start=1):
        stamped.append(with_event_hash({**event, "seq": index}, prev_event_hash=prev))
        prev = str(stamped[-1]["event_hash"])
    return stamped


def test_fresh_immediately_after_issue() -> None:
    events = _stamp(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "test", "authority": "owner"},
                "data": {"goal": {}},
            },
            _issued(),
        ],
    )
    issue = events[1]
    assert is_fresh(events, issue_seq=2, issue_hash=str(issue["event_hash"]))


def test_observation_invalidates_freshness() -> None:
    events = _stamp(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "test", "authority": "owner"},
                "data": {"goal": {}},
            },
            _issued(),
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_3",
                "type": "observation.recorded",
                "time": "2026-07-04T18:02:00Z",
                "goal_id": "goal_01",
                "source": "executor://test",
                "actor": {"type": "executor", "id": "exec", "authority": "operator"},
                "data": {"observations": []},
            },
        ],
    )
    issue = events[1]
    assert not is_fresh(events, issue_seq=2, issue_hash=str(issue["event_hash"]))


def test_evaluate_executable_not_expired() -> None:
    events = _stamp(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "test", "authority": "owner"},
                "data": {"goal": {}},
            },
            _issued(),
        ],
    )
    issue_data = events[1]["data"]
    assert isinstance(issue_data, dict)
    recommendation = issue_data["recommendation"]
    assert isinstance(recommendation, dict)
    check = evaluate_executable(
        events,
        recommendation,
        actor_id="exec",
        now=datetime(2026, 7, 4, 18, 30, tzinfo=UTC),
    )
    assert check.fresh
    assert not check.expired
