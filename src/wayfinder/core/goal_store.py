"""Goal-scoped store combining log, lock, artifacts, and idempotency."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import InvalidInputError
from wayfinder.core.event_log import EventLog
from wayfinder.core.idempotency import IdempotencyStore
from wayfinder.core.lock import AppendLock
from wayfinder.core.reducer import reduce_events
from wayfinder.core.snapshot import load_latest_valid_snapshot, reduce_from_snapshot, write_snapshot
from wayfinder.core.updates import find_recommendation_by_issue, map_update_to_events


@dataclass(frozen=True)
class AppendResult:
    """Result of appending one or more events atomically."""

    events: list[dict[str, Any]]
    seq_start: int
    seq_end: int


class GoalStore:
    """High-level API for a single goal within a wayfinder store."""

    def __init__(self, store_root: Path, goal_id: str) -> None:
        self.store_root = store_root
        self.goal_id = goal_id
        self.event_log = EventLog.for_goal(store_root, goal_id)
        self.lock = AppendLock.for_goal(store_root, goal_id)
        self.artifacts = ArtifactStore.for_goal(store_root, goal_id)
        self.idempotency = IdempotencyStore.for_store(store_root)

    def read_events(self) -> list[dict[str, Any]]:
        return self.event_log.read_all()

    def status(self, *, observed_at: str | None = None) -> dict[str, Any]:
        events = self.read_events()
        snapshot = load_latest_valid_snapshot(self.store_root, self.goal_id, events)
        if snapshot is not None:
            state = reduce_from_snapshot(snapshot, events)
        else:
            state = reduce_events(events)
        timestamp = observed_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return state.to_status(observed_at=timestamp)

    def write_snapshot(self, *, seq: int | None = None) -> dict[str, Any]:
        """Persist a validated snapshot for the current event log."""
        events = self.read_events()
        return write_snapshot(self.store_root, self.goal_id, events, seq=seq)

    def append_events(
        self,
        event_templates: list[dict[str, Any]],
        *,
        holder: str,
    ) -> AppendResult:
        if not event_templates:
            msg = "append requires at least one event"
            raise InvalidInputError(msg)
        with self.lock.acquire(holder):
            return self._append_events_unlocked(event_templates)

    def _append_events_unlocked(self, event_templates: list[dict[str, Any]]) -> AppendResult:
        appended = self.event_log.append_many(event_templates)
        return AppendResult(
            events=appended,
            seq_start=int(appended[0]["seq"]),
            seq_end=int(appended[-1]["seq"]),
        )

    def append_while_locked(self, event_templates: list[dict[str, Any]]) -> AppendResult:
        """Append events when the caller already holds this store's append lock."""
        return self._append_events_unlocked(event_templates)

    def apply_update(
        self,
        update: dict[str, Any],
        *,
        holder: str,
        event_id_factory: Callable[[], str],
        now: datetime | None = None,
    ) -> AppendResult:
        update_id = str(update["update_id"])
        existing = self.idempotency.get_update(update_id)
        if existing is not None:
            digest = IdempotencyStore.canonical_hash(update)
            if existing.canonical_hash != digest:
                msg = f"update_id reused with different content: {update_id}"
                raise InvalidInputError(msg)
            events = self.read_events()
            replayed = [
                event
                for event in events
                if existing.seq_start <= int(event["seq"]) <= existing.seq_end
            ]
            return AppendResult(
                events=replayed,
                seq_start=existing.seq_start,
                seq_end=existing.seq_end,
            )

        events = self.read_events()
        recommendation = None
        if "issued_event_seq" in update and "issued_event_hash" in update:
            recommendation = find_recommendation_by_issue(
                events,
                issued_event_seq=int(update["issued_event_seq"]),
                issued_event_hash=str(update["issued_event_hash"]),
            )
        mapped = map_update_to_events(
            update,
            events=events,
            recommendation=recommendation,
            event_id_factory=event_id_factory,
            now=now,
        )
        result = self.append_events(mapped, holder=holder)
        self.idempotency.put_update(
            update_id,
            update,
            goal_id=self.goal_id,
            seq_start=result.seq_start,
            seq_end=result.seq_end,
        )
        return result
