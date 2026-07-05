"""Snapshot write, validate, and replay (§6.7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.core.hash_chain import CorruptEventLogError
from wayfinder.core.reducer import StatusState, _apply_event, reduce_events


def _state_to_dict(state: StatusState) -> dict[str, Any]:
    return {
        "goal_id": state.goal_id,
        "goal_status": state.goal_status,
        "reason_code": state.reason_code,
        "progress_summary": state.progress_summary,
        "completed_steps": state.completed_steps,
        "last_issued_recommendation_id": state.last_issued_recommendation_id,
        "open_recommendation_id": state.open_recommendation_id,
        "last_event_seq": state.last_event_seq,
        "event_log_head": state.event_log_head,
        "needs": state.needs,
    }


def _state_from_dict(data: dict[str, Any]) -> StatusState:
    return StatusState(
        goal_id=str(data.get("goal_id", "")),
        goal_status=str(data.get("goal_status", "unknown")),
        reason_code=data.get("reason_code"),
        progress_summary=str(data.get("progress_summary", "")),
        completed_steps=int(data.get("completed_steps", 0)),
        last_issued_recommendation_id=data.get("last_issued_recommendation_id"),
        open_recommendation_id=data.get("open_recommendation_id"),
        last_event_seq=int(data.get("last_event_seq", 0)),
        event_log_head=data.get("event_log_head"),
        needs=list(data.get("needs", [])),
    )


def snapshots_dir(store_root: Path, goal_id: str) -> Path:
    """Return the snapshots directory for *goal_id* within *store_root*."""
    return store_root / "goals" / goal_id / "snapshots"


def validate_snapshot(snapshot: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Verify snapshot base matches the event log at *snapshot* seq."""
    seq = int(snapshot["seq"])
    expected_head = str(snapshot["event_log_head"])
    for event in events:
        if int(event["seq"]) != seq:
            continue
        if str(event["event_hash"]) != expected_head:
            msg = f"snapshot event_log_head mismatch at seq {seq}"
            raise CorruptEventLogError(msg)
        return
    msg = f"snapshot seq {seq} not found in event log"
    raise CorruptEventLogError(msg)


def reduce_from_snapshot(
    snapshot: dict[str, Any],
    events: list[dict[str, Any]],
) -> StatusState:
    """Replay from a validated snapshot plus later events."""
    validate_snapshot(snapshot, events)
    state = _state_from_dict(snapshot["state"])
    after_seq = int(snapshot["seq"])
    for event in events:
        if int(event["seq"]) <= after_seq:
            continue
        _apply_event(state, event)
    return state


def write_snapshot(
    store_root: Path,
    goal_id: str,
    events: list[dict[str, Any]],
    *,
    seq: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Write a snapshot file for *goal_id* at *seq* (default: full log length)."""
    if not events:
        msg = "cannot snapshot an empty event log"
        raise CorruptEventLogError(msg)
    target_seq = len(events) if seq is None else seq
    if target_seq < 1 or target_seq > len(events):
        msg = f"snapshot seq out of range: {target_seq}"
        raise CorruptEventLogError(msg)
    prefix = events[:target_seq]
    state = reduce_events(prefix)
    anchor = prefix[-1]
    snapshot: dict[str, Any] = {
        "schema": "wip.snapshot/0.1",
        "protocol_version": "0.1",
        "goal_id": goal_id,
        "seq": target_seq,
        "event_log_head": str(anchor["event_hash"]),
        "created_at": created_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": _state_to_dict(state),
    }
    validate_snapshot(snapshot, events)
    directory = snapshots_dir(store_root, goal_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{target_seq:08d}.json"
    payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")
    return snapshot


def load_latest_valid_snapshot(
    store_root: Path,
    goal_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the newest valid snapshot for *goal_id*, if any."""
    directory = snapshots_dir(store_root, goal_id)
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), reverse=True)
    for path in candidates:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            continue
        try:
            validate_snapshot(loaded, events)
        except (CorruptEventLogError, KeyError, TypeError, ValueError):
            continue
        return loaded
    return None
