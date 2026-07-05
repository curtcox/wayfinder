"""Append-only JSONL event log (§6.1, §6.5, §6.6)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wayfinder.core.hash_chain import (
    CorruptEventLogError,
    verify_event_hash,
    verify_hash_chain,
    with_event_hash,
)


def _verify_chained_event(
    event: dict[str, Any],
    *,
    expected_seq: int,
    prev_hash: str | None,
) -> str:
    """Validate one event's seq, prev link, and hash; return its event_hash."""
    seq = event.get("seq")
    if not isinstance(seq, int):
        msg = "event seq must be an integer"
        raise CorruptEventLogError(msg)
    if seq != expected_seq:
        msg = f"invalid event seq ordering: expected {expected_seq}, got {seq}"
        raise CorruptEventLogError(msg)
    if seq == 1:
        if event.get("prev_event_hash") is not None:
            msg = "first event prev_event_hash must be null"
            raise CorruptEventLogError(msg)
    elif event.get("prev_event_hash") != prev_hash:
        msg = "prev_event_hash mismatch"
        raise CorruptEventLogError(msg)
    if not verify_event_hash(event):
        msg = "event_hash mismatch"
        raise CorruptEventLogError(msg)
    return str(event["event_hash"])


@dataclass(frozen=True)
class EventLogHead:
    """Current log head metadata."""

    seq: int
    event_hash: str | None


class EventLog:
    """Goal-scoped append-only event log backed by events.ndjson."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_goal(cls, store_root: Path, goal_id: str) -> EventLog:
        return cls(store_root / "goals" / goal_id / "events.ndjson")

    def read_all(self) -> list[dict[str, Any]]:
        """Read and parse all complete JSONL lines."""
        events = self._read_all_unchecked()
        verify_hash_chain(events)
        return events

    def head(self) -> EventLogHead:
        events = self.read_all()
        if not events:
            return EventLogHead(seq=0, event_hash=None)
        last = events[-1]
        return EventLogHead(seq=int(last["seq"]), event_hash=str(last["event_hash"]))

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append a single event with hash-chain stamping and durability."""
        return self.append_many([event])[0]

    def append_many(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Append multiple events atomically with hash-chain stamping."""
        if not events:
            return []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_all()
        prev_hash = existing[-1]["event_hash"] if existing else None
        next_seq = (existing[-1]["seq"] + 1) if existing else 1
        stamped_events: list[dict[str, Any]] = []
        lines: list[str] = []
        for event in events:
            stamped = with_event_hash({**event, "seq": next_seq}, prev_event_hash=prev_hash)
            stamped_events.append(stamped)
            lines.append(json.dumps(stamped, separators=(",", ":"), ensure_ascii=False) + "\n")
            prev_hash = stamped["event_hash"]
            next_seq += 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        return stamped_events

    def read_raw_lines_since(self, since_seq: int, *, limit: int | None = None) -> list[str]:
        """Return verbatim stored JSONL lines with seq > since_seq (§1.4)."""
        return list(self.iter_verified_lines_since(since_seq, limit=limit))

    def iter_verified_lines_since(
        self,
        since_seq: int,
        *,
        limit: int | None = None,
    ) -> Iterator[str]:
        """Stream JSONL lines with incremental hash-chain verification (§1.4, §15.35)."""
        if not self.path.exists():
            return
        expected_seq: int | None = None
        prev_hash: str | None = None
        yielded = 0
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    msg = f"partial or invalid JSON at line {line_number}"
                    raise CorruptEventLogError(msg) from exc
                if not isinstance(parsed, dict):
                    msg = f"event at line {line_number} is not an object"
                    raise CorruptEventLogError(msg)
                if expected_seq is None:
                    expected_seq = int(parsed["seq"])
                prev_hash = _verify_chained_event(
                    parsed,
                    expected_seq=expected_seq,
                    prev_hash=prev_hash,
                )
                expected_seq += 1
                seq = int(parsed["seq"])
                if seq <= since_seq:
                    continue
                yield line if line.endswith("\n") else f"{line}\n"
                yielded += 1
                if limit is not None and yielded >= limit:
                    break

    def _read_all_unchecked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    msg = f"partial or invalid JSON at line {line_number}"
                    raise CorruptEventLogError(msg) from exc
                if not isinstance(parsed, dict):
                    msg = f"event at line {line_number} is not an object"
                    raise CorruptEventLogError(msg)
                events.append(parsed)
        return events
