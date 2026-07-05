"""Gather goal and store context for prose front-ends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from wayfinder.cli.store_paths import resolve_store_root
from wayfinder.core.goal_store import GoalStore


class ReadClient(Protocol):
    """Minimal read surface for gathering goal context."""

    def status(self, goal_id: str) -> dict[str, Any]: ...

    def history(self, goal_id: str, *, since_seq: int = 0) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class GoalContext:
    """Snapshot of goal state used to compose updates and answers."""

    goal_id: str
    status: dict[str, Any]
    goal: dict[str, Any]
    open_recommendation: dict[str, Any] | None
    open_issue_event: dict[str, Any] | None
    recent_events: list[dict[str, Any]]


def list_goal_ids(store_root: Path) -> list[str]:
    """Return goal ids present under a store root."""
    goals_dir = store_root / "goals"
    if not goals_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in goals_dir.iterdir()
        if path.is_dir() and (path / "events.ndjson").is_file()
    )


def _goal_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event.get("type") != "goal.created":
            continue
        data = event.get("data", {})
        if isinstance(data, dict):
            goal = data.get("goal")
            if isinstance(goal, dict):
                return goal
    return {}


def _open_recommendation_from_events(
    events: list[dict[str, Any]],
    open_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not open_id:
        return None, None
    recommendation: dict[str, Any] | None = None
    issue_event: dict[str, Any] | None = None
    for event in events:
        if event.get("type") != "recommendation.issued":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        rec = data.get("recommendation")
        if isinstance(rec, dict) and rec.get("recommendation_id") == open_id:
            recommendation = rec
            issue_event = event
    return recommendation, issue_event


def gather_goal_context(
    client: ReadClient,
    goal_id: str,
    *,
    history_limit: int = 40,
) -> GoalContext:
    """Load status and recent history for one goal."""
    status = client.status(goal_id)
    events = client.history(goal_id, since_seq=0)
    recent = events[-history_limit:] if len(events) > history_limit else events
    open_id = status.get("open_recommendation_id")
    open_id_str = str(open_id) if open_id else None
    open_rec, issue_event = _open_recommendation_from_events(events, open_id_str)
    goal = _goal_from_events(events)
    return GoalContext(
        goal_id=goal_id,
        status=status,
        goal=goal,
        open_recommendation=open_rec,
        open_issue_event=issue_event,
        recent_events=recent,
    )


def gather_store_context(
    *,
    store: str | None = None,
    history_limit: int = 20,
) -> list[dict[str, Any]]:
    """Summarize every goal in a store for store-wide ask mode."""
    store_root = resolve_store_root(store)
    summaries: list[dict[str, Any]] = []
    for goal_id in list_goal_ids(store_root):
        goal_store = GoalStore(store_root, goal_id)
        status = goal_store.status()
        events = goal_store.read_events()
        recent = events[-history_limit:] if len(events) > history_limit else events
        summaries.append(
            {
                "goal_id": goal_id,
                "status": status,
                "goal": _goal_from_events(events),
                "recent_events": recent,
            },
        )
    return summaries
