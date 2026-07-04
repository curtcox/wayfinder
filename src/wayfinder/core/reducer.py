"""Deterministic status replay (§7.5)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from wayfinder.core.hash_chain import CorruptEventLogError, verify_hash_chain
from wayfinder.core.types import (
    CLEAR_OPEN_RECOMMENDATION_EVENT_TYPES,
    RESERVED_EVENT_TYPES,
    TERMINAL_ACTION_EVENT_TYPES,
    is_terminal_goal_status,
)


@dataclass
class StatusState:
    """Reduced visible status fields."""

    goal_id: str = ""
    goal_status: str = "unknown"
    reason_code: str | None = None
    progress_summary: str = ""
    completed_steps: int = 0
    last_issued_recommendation_id: str | None = None
    open_recommendation_id: str | None = None
    last_event_seq: int = 0
    event_log_head: str | None = None
    needs: list[dict[str, Any]] = field(default_factory=list)

    def to_status(self, *, observed_at: str) -> dict[str, Any]:
        return {
            "schema": "wip.status/0.1",
            "protocol_version": "0.1",
            "goal_id": self.goal_id,
            "run_id": None,
            "observed_at": observed_at,
            "goal_status": self.goal_status,
            "reason_code": self.reason_code,
            "progress": {
                "summary": self.progress_summary,
                "percent": None,
                "completed_steps": self.completed_steps,
                "known_remaining_steps": None,
            },
            "last_issued_recommendation_id": self.last_issued_recommendation_id,
            "open_recommendation_id": self.open_recommendation_id,
            "last_event_seq": self.last_event_seq,
            "event_log_head": self.event_log_head,
            "needs": deepcopy(self.needs),
        }


def _event_targets_open(event: dict[str, Any], open_id: str | None) -> bool:
    if open_id is None:
        return False
    data = event.get("data", {})
    if not isinstance(data, dict):
        return False
    return data.get("recommendation_id") == open_id


def _apply_event(state: StatusState, event: dict[str, Any]) -> None:
    event_type = str(event["type"])
    data = event.get("data")
    if not isinstance(data, dict):
        msg = f"invalid event data for {event_type}"
        raise CorruptEventLogError(msg)

    state.last_event_seq = int(event["seq"])
    state.event_log_head = str(event["event_hash"])
    if not state.goal_id:
        state.goal_id = str(event["goal_id"])

    if event_type in RESERVED_EVENT_TYPES:
        msg = f"reserved event type encountered: {event_type}"
        raise CorruptEventLogError(msg)

    if event_type == "goal.created":
        goal = data.get("goal")
        if isinstance(goal, dict):
            state.goal_status = str(goal.get("goal_status", "pending"))
        else:
            state.goal_status = "pending"
        return

    if event_type == "recommendation.issued":
        recommendation = data.get("recommendation")
        if not isinstance(recommendation, dict):
            msg = "recommendation.issued missing recommendation payload"
            raise CorruptEventLogError(msg)
        rec_id = str(recommendation["recommendation_id"])
        state.last_issued_recommendation_id = rec_id
        if not is_terminal_goal_status(state.goal_status):
            state.goal_status = str(recommendation.get("goal_status", state.goal_status))
        if recommendation.get("executable") is True:
            if state.open_recommendation_id is not None:
                msg = "executable recommendation issued while another is open"
                raise CorruptEventLogError(msg)
            state.open_recommendation_id = rec_id
        rec_type = recommendation.get("recommendation_type")
        if rec_type == "question" and not is_terminal_goal_status(state.goal_status):
            state.goal_status = "waiting"
            state.reason_code = "needs_user_input"
        return

    if (
        event_type in CLEAR_OPEN_RECOMMENDATION_EVENT_TYPES
        and _event_targets_open(event, state.open_recommendation_id)
    ):
        state.open_recommendation_id = None

    if event_type == "action.started" and not is_terminal_goal_status(state.goal_status):
        state.goal_status = "running"

    if event_type == "executor.policy_denied" and not is_terminal_goal_status(state.goal_status):
        state.goal_status = "blocked"
        state.reason_code = "policy_denied"

    if event_type == "recommendation.overridden":
        override = data.get("override")
        if isinstance(override, dict) and override.get("decision") == "mark_blocked":
            if not is_terminal_goal_status(state.goal_status):
                state.goal_status = "blocked"
                state.reason_code = override.get("reason_code")

    if event_type == "goal.completed":
        terminal_status = data.get("terminal_status", "succeeded")
        state.goal_status = str(terminal_status)
        state.open_recommendation_id = None

    if event_type == "goal.cancelled":
        state.goal_status = "cancelled"
        state.open_recommendation_id = None

    if event_type in TERMINAL_ACTION_EVENT_TYPES:
        state.completed_steps += 1


def reduce_events(events: list[dict[str, Any]]) -> StatusState:
    """Replay events into a StatusState without consulting wall-clock time."""
    verify_hash_chain(events)
    state = StatusState()
    for event in events:
        _apply_event(state, event)
    return state
