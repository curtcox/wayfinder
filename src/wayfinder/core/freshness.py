"""Recommendation freshness, lease, and claim evaluation (§4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from wayfinder.core.errors import StaleRecommendationError, StorageConflictError
from wayfinder.core.types import TERMINAL_ACTION_EVENT_TYPES, effective_invalidates


@dataclass(frozen=True)
class ExecutableCheck:
    """Result of evaluating §4.2 executable conditions."""

    fresh: bool
    expired: bool
    claimed_by_other: bool
    has_terminal_action: bool
    superseded: bool


def _parse_rfc3339(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _find_issue_event(
    events: list[dict[str, Any]],
    recommendation_id: str,
) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") != "recommendation.issued":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        recommendation = data.get("recommendation", {})
        if (
            isinstance(recommendation, dict)
            and recommendation.get("recommendation_id") == recommendation_id
        ):
            return event
    return None


def is_fresh(events: list[dict[str, Any]], *, issue_seq: int, issue_hash: str) -> bool:
    """Evaluate §4.2 condition 3 without using wall-clock time."""
    seen_issue = False
    for event in events:
        seq = int(event["seq"])
        if seq < issue_seq:
            continue
        if seq == issue_seq:
            if event.get("event_hash") != issue_hash:
                return False
            seen_issue = True
            continue
        if not seen_issue:
            return False
        if effective_invalidates(event):
            return False
    return True


def evaluate_executable(
    events: list[dict[str, Any]],
    recommendation: dict[str, Any],
    *,
    actor_id: str,
    now: datetime | None = None,
) -> ExecutableCheck:
    """Evaluate §4.2 conditions 1-7 for an executable recommendation."""
    recommendation_id = str(recommendation["recommendation_id"])
    issue_event = _find_issue_event(events, recommendation_id)
    if issue_event is None:
        return ExecutableCheck(
            fresh=False,
            expired=False,
            claimed_by_other=False,
            has_terminal_action=False,
            superseded=False,
        )

    issue_hash = str(issue_event["event_hash"])
    issue_seq = int(issue_event["seq"])
    fresh = recommendation.get("executable") is True and is_fresh(
        events,
        issue_seq=issue_seq,
        issue_hash=issue_hash,
    )

    clock = now or datetime.now(tz=UTC)
    expires_at = _parse_rfc3339(str(recommendation["expires_at"]))
    lease = recommendation.get("lease")
    lease_expires = expires_at
    if isinstance(lease, dict) and "lease_expires_at" in lease:
        lease_expires = _parse_rfc3339(str(lease["lease_expires_at"]))
    expired = clock >= expires_at or clock >= lease_expires

    action_id = None
    action = recommendation.get("action")
    if isinstance(action, dict):
        action_id = action.get("action_id")

    has_terminal = False
    superseded = False
    claimed_by_other = False
    for event in events:
        if event.get("type") == "recommendation.superseded":
            data = event.get("data", {})
            if isinstance(data, dict) and data.get("recommendation_id") == recommendation_id:
                superseded = True
        if event.get("type") in TERMINAL_ACTION_EVENT_TYPES:
            data = event.get("data", {})
            if (
                isinstance(data, dict)
                and data.get("recommendation_id") == recommendation_id
                and (action_id is None or data.get("action_id") == action_id)
            ):
                has_terminal = True
        if event.get("type") in {"recommendation.accepted", "action.started"}:
            data = event.get("data", {})
            actor = event.get("actor", {})
            if (
                isinstance(data, dict)
                and data.get("recommendation_id") == recommendation_id
                and isinstance(actor, dict)
                and actor.get("id") != actor_id
            ):
                claimed_by_other = True

    return ExecutableCheck(
        fresh=fresh,
        expired=expired,
        claimed_by_other=claimed_by_other,
        has_terminal_action=has_terminal,
        superseded=superseded,
    )


def assert_can_start_action(
    events: list[dict[str, Any]],
    recommendation: dict[str, Any],
    *,
    actor_id: str,
    now: datetime | None = None,
) -> None:
    """Raise if an executor may not accept/start the recommendation."""
    check = evaluate_executable(events, recommendation, actor_id=actor_id, now=now)
    if check.claimed_by_other:
        msg = "recommendation already claimed by another actor"
        raise StorageConflictError(msg)
    if check.has_terminal_action or check.superseded or not check.fresh or check.expired:
        msg = "recommendation is stale"
        raise StaleRecommendationError(msg)


def has_action_started(
    events: list[dict[str, Any]],
    *,
    recommendation_id: str,
    action_id: str,
) -> bool:
    for event in events:
        if event.get("type") != "action.started":
            continue
        data = event.get("data", {})
        if (
            isinstance(data, dict)
            and data.get("recommendation_id") == recommendation_id
            and data.get("action_id") == action_id
        ):
            return True
    return False


def assert_can_submit_terminal_result(
    events: list[dict[str, Any]],
    *,
    recommendation_id: str,
    action_id: str,
    actor_id: str,
) -> None:
    """Apply §4.2 terminal-result acceptance rule."""
    started = False
    started_by_actor = False
    has_terminal = False
    for event in events:
        if event.get("type") == "action.started":
            data = event.get("data", {})
            if (
                isinstance(data, dict)
                and data.get("recommendation_id") == recommendation_id
                and data.get("action_id") == action_id
            ):
                started = True
                actor = event.get("actor", {})
                if isinstance(actor, dict) and actor.get("id") == actor_id:
                    started_by_actor = True
        if event.get("type") in TERMINAL_ACTION_EVENT_TYPES:
            data = event.get("data", {})
            if (
                isinstance(data, dict)
                and data.get("recommendation_id") == recommendation_id
                and data.get("action_id") == action_id
            ):
                has_terminal = True
    if has_terminal:
        msg = "terminal action event already exists"
        raise StaleRecommendationError(msg)
    if started and not started_by_actor:
        msg = "action started by a different actor"
        raise StorageConflictError(msg)
    if not started:
        msg = "action has not started"
        raise StaleRecommendationError(msg)
