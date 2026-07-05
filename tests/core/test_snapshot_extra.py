"""Snapshot validation edge-case tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, service_for_store
from wayfinder.core.goal_store import GoalStore
from wayfinder.core.hash_chain import CorruptEventLogError
from wayfinder.core.snapshot import load_latest_valid_snapshot, validate_snapshot, write_snapshot


def test_write_snapshot_rejects_empty_log(tmp_path: Path) -> None:
    with pytest.raises(CorruptEventLogError, match="empty event log"):
        write_snapshot(tmp_path, "goal_01", [])


def test_write_snapshot_rejects_out_of_range_seq(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    events = GoalStore(store, goal_id).read_events()
    with pytest.raises(CorruptEventLogError, match="out of range"):
        write_snapshot(store, goal_id, events, seq=99)


def test_load_latest_valid_snapshot_skips_invalid(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    events = GoalStore(store, goal_id).read_events()
    snapshot_dir = store / "goals" / goal_id / "snapshots"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "00000001.json").write_text('{"seq": 1}', encoding="utf-8")
    (snapshot_dir / "00000002.json").write_text('"not-a-snapshot"', encoding="utf-8")
    assert load_latest_valid_snapshot(store, goal_id, events) is None


def test_validate_snapshot_detects_head_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    events = GoalStore(store, goal_id).read_events()
    snapshot = write_snapshot(store, goal_id, events, seq=1)
    snapshot["event_log_head"] = "sha256:" + "0" * 64
    with pytest.raises(CorruptEventLogError, match="mismatch"):
        validate_snapshot(snapshot, events)


def test_validate_snapshot_detects_missing_seq(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    events = GoalStore(store, goal_id).read_events()
    with pytest.raises(CorruptEventLogError, match="not found"):
        validate_snapshot({"seq": 99, "event_log_head": str(events[0]["event_hash"])}, events)
