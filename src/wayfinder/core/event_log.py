"""Append-only JSONL event log (§6.1, §6.5, §6.6)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wayfinder.core.hash_chain import CorruptEventLogError, verify_hash_chain, with_event_hash


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_all()
        prev_hash = existing[-1]["event_hash"] if existing else None
        next_seq = (existing[-1]["seq"] + 1) if existing else 1
        stamped = with_event_hash({**event, "seq": next_seq}, prev_event_hash=prev_hash)
        line = json.dumps(stamped, separators=(",", ":"), ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return stamped

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
