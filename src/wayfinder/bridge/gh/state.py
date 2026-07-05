"""Persisted bridge sync state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BridgeState:
    """Tracks GitHub issue linkage and sync cursors."""

    issue_number: int | None = None
    last_synced_seq: int = 0
    processed_comment_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BridgeState:
        issue_number = payload.get("issue_number")
        parsed_issue = int(issue_number) if issue_number is not None else None
        comment_ids = payload.get("processed_comment_ids", [])
        parsed_comments = [int(item) for item in comment_ids] if isinstance(comment_ids, list) else []
        return cls(
            issue_number=parsed_issue,
            last_synced_seq=int(payload.get("last_synced_seq", 0)),
            processed_comment_ids=parsed_comments,
        )


def state_path(store_root: Path, goal_id: str) -> Path:
    return store_root / "bridge" / "gh" / f"{goal_id}.json"


def load_state(store_root: Path, goal_id: str) -> BridgeState:
    path = state_path(store_root, goal_id)
    if not path.is_file():
        return BridgeState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return BridgeState()
    return BridgeState.from_dict(payload)


def save_state(store_root: Path, goal_id: str, state: BridgeState) -> None:
    path = state_path(store_root, goal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
