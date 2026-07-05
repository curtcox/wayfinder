"""Tests for append-only event logs."""

from __future__ import annotations

import json
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


def test_iter_verified_lines_since_filters_and_limits(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.append(_event_template(event_id="evt_00000001"))
    log.append(_event_template(event_id="evt_00000002", type="observation.recorded"))
    log.append(_event_template(event_id="evt_00000003", type="observation.recorded"))
    lines = list(log.iter_verified_lines_since(1, limit=1))
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["seq"] == 2


def test_iter_verified_lines_since_detects_corruption(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.append(_event_template(event_id="evt_00000001"))
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write("not-valid-json\n")
    with pytest.raises(CorruptEventLogError, match="partial or invalid JSON"):
        list(log.iter_verified_lines_since(0))


def test_read_raw_lines_since_returns_verbatim_tail(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.append(_event_template(event_id="evt_00000001"))
    log.append(_event_template(event_id="evt_00000002", type="observation.recorded"))
    lines = log.read_raw_lines_since(1)
    assert len(lines) == 1
    assert json.loads(lines[0])["seq"] == 2


def test_append_many_empty_returns_empty(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    assert log.append_many([]) == []


def test_head_on_empty_log(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    head = log.head()
    assert head.seq == 0
    assert head.event_hash is None


def test_iter_verified_lines_since_on_missing_path(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    assert list(log.iter_verified_lines_since(0)) == []


def test_read_all_rejects_non_object_line(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text("[1,2,3]\n", encoding="utf-8")
    with pytest.raises(CorruptEventLogError, match="not an object"):
        log.read_all()


def test_iter_verified_lines_since_rejects_non_object_line(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text('"not-an-object"\n', encoding="utf-8")
    with pytest.raises(CorruptEventLogError, match="not an object"):
        list(log.iter_verified_lines_since(0))


def test_iter_verified_lines_since_normalizes_missing_trailing_newline(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    stamped = log.append(_event_template(event_id="evt_00000001"))
    with log.path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(stamped, separators=(",", ":"), ensure_ascii=False))
    lines = list(log.iter_verified_lines_since(0))
    assert len(lines) == 1
    assert lines[0].endswith("\n")


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"seq": "1"}, "seq must be an integer"),
        ({"seq": 1, "prev_event_hash": "sha256:bad", "event_hash": "sha256:00"}, "prev_event_hash"),
        ({"seq": 1, "prev_event_hash": None, "event_hash": "sha256:bad"}, "event_hash mismatch"),
    ],
)
def test_iter_verified_lines_since_detects_chain_errors(
    tmp_path: Path,
    payload: dict[str, object],
    match: str,
) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            **payload,
        },
        separators=(",", ":"),
    )
    log.path.write_text(f"{line}\n", encoding="utf-8")
    with pytest.raises(CorruptEventLogError, match=match):
        list(log.iter_verified_lines_since(0))


def test_read_all_skips_blank_lines(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    first = log.append(_event_template(event_id="evt_00000001"))
    second = log.append(_event_template(event_id="evt_00000002", type="observation.recorded"))
    with log.path.open("w", encoding="utf-8") as handle:
        handle.write("\n\n")
        handle.write(json.dumps(first, separators=(",", ":"), ensure_ascii=False) + "\n")
        handle.write("\n")
        handle.write(json.dumps(second, separators=(",", ":"), ensure_ascii=False) + "\n")
    events = log.read_all()
    assert [event["event_id"] for event in events] == [first["event_id"], second["event_id"]]


def test_iter_verified_lines_since_detects_seq_ordering(tmp_path: Path) -> None:
    log = EventLog.for_goal(tmp_path, "goal_01")
    log.append(_event_template(event_id="evt_00000001"))
    bad_second = json.dumps(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "seq": 3,
            "prev_event_hash": "sha256:00",
            "event_hash": "sha256:00",
        },
        separators=(",", ":"),
    )
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write(f"{bad_second}\n")
    with pytest.raises(CorruptEventLogError, match="ordering"):
        list(log.iter_verified_lines_since(0))
