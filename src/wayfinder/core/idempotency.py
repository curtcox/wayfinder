"""Idempotency persistence for goal create and updates (§1.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wayfinder.core.canonical import canonical_bytes, sha256_digest, strip_transport_fields
from wayfinder.core.errors import InvalidInputError


@dataclass(frozen=True)
class CreateIdRecord:
    canonical_hash: str
    goal_id: str
    created_event_seq: int


@dataclass(frozen=True)
class UpdateIdRecord:
    canonical_hash: str
    goal_id: str
    seq_start: int
    seq_end: int


class IdempotencyStore:
    """Store keyed by create_id and update_id within a wayfinder store."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_store(cls, store_root: Path) -> IdempotencyStore:
        return cls(store_root / "idempotency.json")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"create": {}, "update": {}}
        data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.path)
        self.path.chmod(0o600)

    @staticmethod
    def canonical_hash(payload: dict[str, Any]) -> str:
        return sha256_digest(canonical_bytes(strip_transport_fields(payload)))

    def get_create(self, create_id: str) -> CreateIdRecord | None:
        raw = self._load()["create"].get(create_id)
        if raw is None:
            return None
        return CreateIdRecord(
            canonical_hash=str(raw["canonical_hash"]),
            goal_id=str(raw["goal_id"]),
            created_event_seq=int(raw["created_event_seq"]),
        )

    def put_create(
        self,
        create_id: str,
        payload: dict[str, Any],
        *,
        goal_id: str,
        event_seq: int,
    ) -> None:
        data = self._load()
        digest = self.canonical_hash(payload)
        existing = data["create"].get(create_id)
        if existing is not None and existing["canonical_hash"] != digest:
            msg = f"create_id reused with different content: {create_id}"
            raise InvalidInputError(msg)
        data["create"][create_id] = {
            "canonical_hash": digest,
            "goal_id": goal_id,
            "created_event_seq": event_seq,
        }
        self._save(data)

    def get_update(self, update_id: str) -> UpdateIdRecord | None:
        raw = self._load()["update"].get(update_id)
        if raw is None:
            return None
        return UpdateIdRecord(
            canonical_hash=str(raw["canonical_hash"]),
            goal_id=str(raw["goal_id"]),
            seq_start=int(raw["seq_start"]),
            seq_end=int(raw["seq_end"]),
        )

    def put_update(
        self,
        update_id: str,
        payload: dict[str, Any],
        *,
        goal_id: str,
        seq_start: int,
        seq_end: int,
    ) -> None:
        data = self._load()
        digest = self.canonical_hash(payload)
        existing = data["update"].get(update_id)
        if existing is not None and existing["canonical_hash"] != digest:
            msg = f"update_id reused with different content: {update_id}"
            raise InvalidInputError(msg)
        data["update"][update_id] = {
            "canonical_hash": digest,
            "goal_id": goal_id,
            "seq_start": seq_start,
            "seq_end": seq_end,
        }
        self._save(data)
