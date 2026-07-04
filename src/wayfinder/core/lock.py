"""Pinned append lock primitive (§6.5)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


from wayfinder.core.errors import StorageConflictError


@dataclass(frozen=True)
class LockBody:
    """JSON body written to append.lock."""

    holder: str
    pid: int
    acquired_at: str
    expires_at: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "holder": self.holder,
                "pid": self.pid,
                "acquired_at": self.acquired_at,
                "expires_at": self.expires_at,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> LockBody:
        data = json.loads(raw)
        return cls(
            holder=str(data["holder"]),
            pid=int(data["pid"]),
            acquired_at=str(data["acquired_at"]),
            expires_at=str(data["expires_at"]),
        )


def _parse_rfc3339(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


@dataclass
class AppendLock:
    """Per-goal append lock using O_CREAT|O_EXCL."""

    path: Path
    ttl: timedelta = timedelta(minutes=5)

    @classmethod
    def for_goal(
        cls,
        store_root: Path,
        goal_id: str,
        *,
        ttl: timedelta = timedelta(minutes=5),
    ) -> AppendLock:
        return cls(store_root / "goals" / goal_id / "locks" / "append.lock", ttl=ttl)

    def _is_expired(self, body: LockBody) -> bool:
        return _parse_rfc3339(body.expires_at) <= datetime.now(tz=UTC)

    def _try_break_stale(self) -> None:
        if not self.path.exists():
            return
        try:
            body = LockBody.from_json(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            msg = "unparseable append lock body"
            raise StorageConflictError(msg) from exc
        if not self._is_expired(body):
            msg = "append lock held by another writer"
            raise StorageConflictError(msg)
        self.path.unlink()

    @contextmanager
    def acquire(self, holder: str) -> Generator[None, None, None]:
        """Acquire the lock, yielding while held; released on exit."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._try_break_stale()
        now = datetime.now(tz=UTC)
        body = LockBody(
            holder=holder,
            pid=os.getpid(),
            acquired_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=(now + self.ttl).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            msg = "append lock held by another writer"
            raise StorageConflictError(msg) from None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body.to_json())
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        try:
            yield
        finally:
            self.path.unlink(missing_ok=True)
