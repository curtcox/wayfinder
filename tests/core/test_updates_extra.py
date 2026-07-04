"""Additional update mapping coverage."""

from __future__ import annotations

from datetime import UTC, datetime

from wayfinder.core.updates import map_update_to_events


def _next_event_id() -> str:
    return "evt_extra"


def test_map_observation_update() -> None:
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_obs",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "operator", "authenticated": True},
        "update_type": "observation",
        "observations": [{"kind": "message", "text": "hello"}],
    }
    mapped = map_update_to_events(
        update,
        events=[],
        recommendation=None,
        event_id_factory=_next_event_id,
    )
    assert mapped[0]["type"] == "observation.recorded"


def test_map_heartbeat_update() -> None:
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_hb",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator"},
        "update_type": "heartbeat",
        "heartbeat": {"status": "running", "observed_at": "2026-07-04T18:00:00Z"},
    }
    mapped = map_update_to_events(
        update,
        events=[],
        recommendation=None,
        event_id_factory=_next_event_id,
        now=datetime(2026, 7, 4, 18, 0, tzinfo=UTC),
    )
    assert mapped[0]["type"] == "executor.heartbeat"
    assert mapped[0]["invalidates_open_recommendations"] is False
