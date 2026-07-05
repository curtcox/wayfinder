"""Append lock tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wayfinder.core.errors import StorageConflictError
from wayfinder.core.lock import AppendLock, LockBody


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = AppendLock.for_goal(tmp_path, "goal_01")
    with lock.acquire("writer"):
        assert lock.path.exists()
    assert not lock.path.exists()


def test_unparseable_lock_body_raises(tmp_path: Path) -> None:
    lock = AppendLock.for_goal(tmp_path, "goal_01")
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("not-json", encoding="utf-8")
    with pytest.raises(StorageConflictError, match="unparseable"):
        with lock.acquire("writer"):
            pass


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    lock = AppendLock.for_goal(tmp_path, "goal_01", ttl=timedelta(seconds=1))
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    expired = datetime.now(tz=UTC) - timedelta(minutes=1)
    body = LockBody(
        holder="stale",
        pid=1,
        acquired_at=expired.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expired.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    lock.path.write_text(body.to_json(), encoding="utf-8")
    with lock.acquire("fresh"):
        assert lock.path.exists()
