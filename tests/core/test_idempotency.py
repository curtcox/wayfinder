"""Idempotency store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.errors import InvalidInputError
from wayfinder.core.idempotency import IdempotencyStore


def test_create_id_reuse_with_different_bytes(tmp_path: Path) -> None:
    store = IdempotencyStore.for_store(tmp_path)
    payload = {
        "schema": "wip.goal_create/0.1",
        "protocol_version": "0.1",
        "create_id": "create_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner"},
        "description": "one",
        "workspace_uri": "file:/tmp/project",
    }
    store.put_create("create_01", payload, goal_id="goal_01", event_seq=1)
    changed = {**payload, "description": "two"}
    with pytest.raises(InvalidInputError):
        store.put_create("create_01", changed, goal_id="goal_01", event_seq=1)
