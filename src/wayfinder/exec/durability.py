"""Durability store for interruption recovery (§11.2-§11.3)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PendingAction:
    """Locally persisted action state for crash recovery."""

    goal_id: str
    recommendation_id: str
    action_id: str
    issued_event_seq: int
    issued_event_hash: str
    accept_update_id: str
    start_update_id: str
    result_update_id: str
    stage: str
    action_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "goal_id": self.goal_id,
            "recommendation_id": self.recommendation_id,
            "action_id": self.action_id,
            "issued_event_seq": self.issued_event_seq,
            "issued_event_hash": self.issued_event_hash,
            "accept_update_id": self.accept_update_id,
            "start_update_id": self.start_update_id,
            "result_update_id": self.result_update_id,
            "stage": self.stage,
        }
        if self.action_result is not None:
            payload["action_result"] = self.action_result
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PendingAction:
        return cls(
            goal_id=str(payload["goal_id"]),
            recommendation_id=str(payload["recommendation_id"]),
            action_id=str(payload["action_id"]),
            issued_event_seq=int(payload["issued_event_seq"]),
            issued_event_hash=str(payload["issued_event_hash"]),
            accept_update_id=str(payload["accept_update_id"]),
            start_update_id=str(payload["start_update_id"]),
            result_update_id=str(payload["result_update_id"]),
            stage=str(payload["stage"]),
            action_result=(
                dict(payload["action_result"])
                if isinstance(payload.get("action_result"), dict)
                else None
            ),
        )


class DurabilityStore:
    """Persist pending action metadata before spawning child processes."""

    def __init__(self, root: Path, *, executor_id: str) -> None:
        self._path = root / "executor-state" / f"{executor_id}.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> PendingAction | None:
        if not self._path.exists():
            return None
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return PendingAction.from_dict(payload)

    def save(self, pending: PendingAction) -> None:
        temp = self._path.with_suffix(".tmp")
        data = json.dumps(pending.to_dict(), separators=(",", ":"), ensure_ascii=False)
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(self._path)
        with self._path.open("rb") as handle:
            os.fsync(handle.fileno())

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
