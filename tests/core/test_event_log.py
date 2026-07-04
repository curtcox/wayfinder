"""Tests for append-only event logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.errors import StorageConflictError
from wayfinder.core.event_log import EventLog
from wayfinder.core.hash_chain import CorruptEventLogError
from wayfinder.core.lock import AppendLock


def _event_template(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_00000001",
        "type": "goal.created",
        "time": "2026-07-04T18:00:00Z",
        "goal_id": "goal_01",
        "source": "wayfinder://test",
        "actor": {"type": "human", "id": "test", "authority": "owner"},
        "data": {},
    }
    base.update(overrides)
    return base


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    first = log.append(_event_template(event_id="evt_00000001"))
    second = log.append(_event_template(event_id="evt_00000002", type="observation.recorded"))
    events = log.read_all()
    assert [event["event_id"] for event in events] == [first["event_id"], second["event_id"]]
    assert events[0]["seq"] == 1
    assert events[1]["seq"] == 2
    assert events[1]["prev_event_hash"] == events[0]["event_hash"]


def test_append_many_atomic(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    appended = log.append_many(
        [
            _event_template(event_id="evt_00000001"),
            _event_template(event_id="evt_00000002", type="observation.recorded"),
        ],
    )
    assert len(appended) == 2
    assert appended[1]["prev_event_hash"] == appended[0]["event_hash"]


def test_corrupt_partial_line_raises(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.path.parent.mkdir(parents=True)
    log.path.write_text('{"schema":"wip.event/0.1","incomplete":', encoding="utf-8")
    with pytest.raises(CorruptEventLogError):
        log.read_all()


def test_append_lock_excludes_concurrent_holder(tmp_path: Path) -> None:
    lock = AppendLock.for_goal(tmp_path, "goal_01")
    with lock.acquire("writer-a"):
        with pytest.raises(StorageConflictError), lock.acquire("writer-b"):
            pass
