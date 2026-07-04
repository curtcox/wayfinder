"""Shared protocol constants."""

from __future__ import annotations

TERMINAL_GOAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
TERMINAL_ACTION_EVENT_TYPES = frozenset(
    {
        "action.completed",
        "action.failed",
        "action.timed_out",
        "action.cancelled",
        "action.blocked",
        "action.skipped",
    },
)
LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "recommendation.accepted",
        "action.started",
        "action.output_recorded",
    },
)
RESERVED_EVENT_TYPES = frozenset(
    {
        "goal.updated",
        "question.asked",
        "wayfinder.status.reported",
    },
)
CLEAR_OPEN_RECOMMENDATION_EVENT_TYPES = frozenset(
    {
        "recommendation.superseded",
        "recommendation.rejected",
        "recommendation.overridden",
        "recommendation.expired",
        *TERMINAL_ACTION_EVENT_TYPES,
        "goal.completed",
        "goal.cancelled",
    },
)

INVALIDATES_DEFAULTS: dict[str, bool] = {
    "goal.created": False,
    "goal.cancelled": True,
    "goal.completed": True,
    "recommendation.issued": True,
    "recommendation.superseded": True,
    "recommendation.accepted": False,
    "recommendation.rejected": True,
    "recommendation.overridden": True,
    "recommendation.expired": True,
    "action.started": False,
    "action.output_recorded": False,
    "observation.recorded": True,
    "correction.recorded": True,
    "approval.requested": False,
    "approval.granted": True,
    "approval.denied": True,
    "question.answered": True,
    "executor.heartbeat": False,
    "executor.policy_denied": True,
    "redaction.recorded": True,
}

for _terminal in TERMINAL_ACTION_EVENT_TYPES:
    INVALIDATES_DEFAULTS[_terminal] = True


def effective_invalidates(event: dict[str, object]) -> bool:
    """Return the effective invalidates_open_recommendations flag for an event."""
    explicit = event.get("invalidates_open_recommendations")
    if explicit is not None:
        return bool(explicit)
    event_type = str(event.get("type", ""))
    if event_type in TERMINAL_ACTION_EVENT_TYPES:
        return True
    return INVALIDATES_DEFAULTS.get(event_type, False)


def is_terminal_goal_status(status: str) -> bool:
    return status in TERMINAL_GOAL_STATUSES
