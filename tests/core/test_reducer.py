"""Reducer replay tests."""

from __future__ import annotations

import copy

from hypothesis import given
from hypothesis import strategies as st

from wayfinder.core.hash_chain import with_event_hash
from wayfinder.core.reducer import reduce_events


def _goal_created(goal_id: str = "goal_01") -> dict[str, object]:
    return {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_00000001",
        "type": "goal.created",
        "time": "2026-07-04T18:00:00Z",
        "goal_id": goal_id,
        "source": "wayfinder://test",
        "actor": {"type": "human", "id": "test", "authority": "owner"},
        "data": {
            "goal": {
                "schema": "wip.goal/0.1",
                "protocol_version": "0.1",
                "goal_id": goal_id,
                "goal_status": "pending",
            },
        },
    }


def _issued_action(rec_id: str = "rec_01") -> dict[str, object]:
    return {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_00000002",
        "type": "recommendation.issued",
        "time": "2026-07-04T18:01:00Z",
        "goal_id": "goal_01",
        "source": "wayfinder://test",
        "actor": {"type": "wayfinder", "id": "test", "authority": "operator"},
        "data": {
            "recommendation": {
                "recommendation_id": rec_id,
                "executable": True,
                "goal_status": "running",
                "recommendation_type": "action",
            },
        },
    }


def _stamp_chain(templates: list[dict[str, object]]) -> list[dict[str, object]]:
    stamped: list[dict[str, object]] = []
    prev: str | None = None
    for index, template in enumerate(templates, start=1):
        event = with_event_hash({**template, "seq": index}, prev_event_hash=prev)
        stamped.append(event)
        prev = str(event["event_hash"])
    return stamped


def test_reduce_goal_created_pending() -> None:
    events = _stamp_chain([_goal_created()])
    state = reduce_events(events)
    assert state.goal_status == "pending"
    assert state.open_recommendation_id is None


def test_reduce_open_recommendation() -> None:
    events = _stamp_chain([_goal_created(), _issued_action()])
    state = reduce_events(events)
    assert state.open_recommendation_id == "rec_01"
    assert state.last_issued_recommendation_id == "rec_01"


def test_reduce_goal_cancelled() -> None:
    events = _stamp_chain(
        [
            _goal_created(),
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_00000002",
                "type": "goal.cancelled",
                "time": "2026-07-04T18:02:00Z",
                "goal_id": "goal_01",
                "source": "human://test",
                "actor": {"type": "human", "id": "test", "authority": "owner", "authenticated": True},
                "data": {"reason": "done"},
            },
        ],
    )
    state = reduce_events(events)
    assert state.goal_status == "cancelled"
    assert state.open_recommendation_id is None


@given(st.integers(min_value=0, max_value=5))
def test_reduce_is_deterministic(extra_observations: int) -> None:
    templates: list[dict[str, object]] = [_goal_created(), _issued_action()]
    for index in range(extra_observations):
        templates.append(
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": f"evt_obs_{index}",
                "type": "observation.recorded",
                "time": "2026-07-04T18:05:00Z",
                "goal_id": "goal_01",
                "source": "executor://test",
                "actor": {"type": "executor", "id": "exec", "authority": "operator"},
                "data": {"observations": []},
            },
        )
    events = _stamp_chain(templates)
    first = reduce_events(events)
    second = reduce_events(copy.deepcopy(events))
    assert first.goal_status == second.goal_status
    assert first.open_recommendation_id == second.open_recommendation_id
