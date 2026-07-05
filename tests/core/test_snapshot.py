"""Snapshot replay tests."""

from __future__ import annotations

from pathlib import Path

from tests.conformance.helpers import append_observations, goal_create_payload, service_for_store
from wayfinder.core.goal_store import GoalStore
from wayfinder.core.reducer import reduce_events
from wayfinder.core.snapshot import reduce_from_snapshot, write_snapshot


def test_snapshot_replay_matches_full_replay(tmp_path: Path) -> None:
    """Replay from snapshot plus suffix matches full event replay."""
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

    goal_store.write_snapshot(seq=50)
    status_from_store = goal_store.status(observed_at="2026-07-04T19:00:00Z")
    status_full = full_state.to_status(observed_at="2026-07-04T19:00:00Z")
    assert status_from_store["goal_status"] == status_full["goal_status"]
    assert status_from_store["last_event_seq"] == status_full["last_event_seq"]
    assert status_from_store["event_log_head"] == status_full["event_log_head"]
