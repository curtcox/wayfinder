"""Update-to-event mapping (§5, §6.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from wayfinder.core.errors import InvalidInputError, PolicyDeniedError, StaleRecommendationError
from wayfinder.core.freshness import (
    assert_can_start_action,
    assert_can_submit_terminal_result,
    evaluate_executable,
    has_action_started,
)

ACTION_RESULT_EVENT_TYPES = {
    "completed": "action.completed",
    "failed": "action.failed",
    "timed_out": "action.timed_out",
    "cancelled": "action.cancelled",
    "blocked": "action.blocked",
    "skipped": "action.skipped",
}

OWNER_AUTHORITY = frozenset({"owner", "policy_admin"})
OPERATOR_AUTHORITY = frozenset({"operator", "owner", "policy_admin"})


def _require_authority(actor: dict[str, Any], allowed: frozenset[str]) -> None:
    authority = str(actor.get("authority", ""))
    authenticated = actor.get("authenticated", False)
    if not authenticated or authority not in allowed:
        msg = "insufficient authority for update"
        raise PolicyDeniedError(msg)


def _event_shell(
    *,
    event_id: str,
    event_type: str,
    goal_id: str,
    time: str,
    source: str,
    actor: dict[str, Any],
    data: dict[str, Any],
    invalidates: bool | None = None,
    subject: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": event_id,
        "type": event_type,
        "time": time,
        "goal_id": goal_id,
        "source": source,
        "actor": actor,
        "data": data,
        "run_id": None,
    }
    if invalidates is not None:
        event["invalidates_open_recommendations"] = invalidates
    if subject is not None:
        event["subject"] = subject
    if correlation_id is not None:
        event["correlation_id"] = correlation_id
    return event


def map_update_to_events(
    update: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    recommendation: dict[str, Any] | None,
    event_id_factory: Any,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Map a wip.update/0.1 object to one or more unstamped event shells."""
    update_type = str(update["update_type"])
    goal_id = str(update["goal_id"])
    actor = update["actor"]
    if not isinstance(actor, dict):
        msg = "update actor must be an object"
        raise InvalidInputError(msg)
    timestamp = (now or datetime.now(tz=UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = f"{actor.get('type', 'system')}://{actor.get('id', 'unknown')}"
    mapped: list[dict[str, Any]] = []

    if update_type == "goal_cancel":
        _require_authority(actor, OWNER_AUTHORITY)
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="goal.cancelled",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "reason": update["goal_cancel"]["reason"],
                    **(
                        {"reason_code": update["goal_cancel"]["reason_code"]}
                        if "reason_code" in update["goal_cancel"]
                        else {}
                    ),
                },
            ),
        )
        return mapped

    if update_type == "recommendation_disposition":
        disposition = update["recommendation_disposition"]["disposition"]
        rec_id = str(update["recommendation_id"])
        data: dict[str, Any] = {
            "recommendation_id": rec_id,
            "disposition": disposition,
        }
        if "action_id" in update:
            data["action_id"] = update["action_id"]
        if "reason" in update["recommendation_disposition"]:
            data["reason"] = update["recommendation_disposition"]["reason"]
        if disposition == "accepted":
            if recommendation is None:
                msg = "accepted disposition requires issued recommendation"
                raise InvalidInputError(msg)
            if recommendation.get("recommendation_type") != "done":
                assert_can_start_action(
                    events,
                    recommendation,
                    actor_id=str(actor["id"]),
                    now=now,
                )
            mapped.append(
                _event_shell(
                    event_id=event_id_factory(),
                    event_type="recommendation.accepted",
                    goal_id=goal_id,
                    time=timestamp,
                    source=source,
                    actor=actor,
                    data=data,
                    invalidates=False,
                    correlation_id=rec_id,
                ),
            )
            if recommendation.get("recommendation_type") == "done":
                mapped.append(
                    _event_shell(
                        event_id=event_id_factory(),
                        event_type="goal.completed",
                        goal_id=goal_id,
                        time=timestamp,
                        source=source,
                        actor=actor,
                        data={"terminal_status": "succeeded"},
                    ),
                )
            return mapped
        if disposition == "rejected":
            event_type = "recommendation.rejected"
        elif disposition == "skipped":
            event_type = "recommendation.rejected"
            data["disposition"] = "skipped"
        elif disposition == "expired":
            if recommendation is None:
                msg = "expired disposition requires recommendation context"
                raise InvalidInputError(msg)
            check = evaluate_executable(events, recommendation, actor_id=str(actor["id"]), now=now)
            if not check.expired:
                msg = "recommendation has not expired"
                raise InvalidInputError(msg)
            event_type = "recommendation.expired"
            data = {
                "recommendation_id": rec_id,
                "expired_at": timestamp,
            }
        else:
            msg = f"unsupported disposition: {disposition}"
            raise InvalidInputError(msg)
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type=event_type,
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data=data,
                correlation_id=rec_id,
            ),
        )
        return mapped

    if update_type == "action_started":
        if recommendation is None:
            msg = "action_started requires issued recommendation"
            raise InvalidInputError(msg)
        assert_can_start_action(events, recommendation, actor_id=str(actor["id"]), now=now)
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="action.started",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "recommendation_id": update["recommendation_id"],
                    "action_id": update["action_id"],
                    "started_at": update["action_started"]["started_at"],
                },
                invalidates=False,
                correlation_id=str(update["recommendation_id"]),
                subject=str(update["action_id"]),
            ),
        )
        return mapped

    if update_type == "action_result":
        rec_id = str(update["recommendation_id"])
        action_id = str(update["action_id"])
        result = update["action_result"]
        status = str(result["status"])
        if has_action_started(events, recommendation_id=rec_id, action_id=action_id):
            assert_can_submit_terminal_result(
                events,
                recommendation_id=rec_id,
                action_id=action_id,
                actor_id=str(actor["id"]),
            )
        else:
            if recommendation is None:
                msg = "action_result requires issued recommendation"
                raise InvalidInputError(msg)
            assert_can_start_action(events, recommendation, actor_id=str(actor["id"]), now=now)
        if status not in ACTION_RESULT_EVENT_TYPES:
            msg = f"unsupported action_result status: {status}"
            raise InvalidInputError(msg)
        event_type = ACTION_RESULT_EVENT_TYPES[status]
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type=event_type,
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "recommendation_id": rec_id,
                    "action_id": action_id,
                    "action_result": result,
                },
                correlation_id=rec_id,
                subject=action_id,
            ),
        )
        return mapped

    if update_type == "observation":
        invalidate = update.get("invalidates_open_recommendations")
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="observation.recorded",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={"observations": update["observations"]},
                invalidates=bool(invalidate) if invalidate is not None else None,
            ),
        )
        return mapped

    if update_type == "correction":
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="correction.recorded",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={"correction": update["correction"]},
            ),
        )
        return mapped

    if update_type == "redaction":
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="redaction.recorded",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={"redaction": update["redaction"]},
            ),
        )
        return mapped

    if update_type == "override":
        decision = update["override"]["decision"]
        if decision in {"mark_done", "mark_failed"}:
            _require_authority(actor, OWNER_AUTHORITY)
        else:
            _require_authority(actor, OPERATOR_AUTHORITY)
        data = {
            "recommendation_id": update.get("recommendation_id"),
            "override": update["override"],
        }
        if "replacement_recommendation" in update["override"]:
            data["replacement_recommendation"] = update["override"]["replacement_recommendation"]
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="recommendation.overridden",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={k: v for k, v in data.items() if v is not None},
                correlation_id=(
                    str(update["recommendation_id"]) if update.get("recommendation_id") else None
                ),
            ),
        )
        if decision == "mark_done":
            mapped.append(
                _event_shell(
                    event_id=event_id_factory(),
                    event_type="goal.completed",
                    goal_id=goal_id,
                    time=timestamp,
                    source=source,
                    actor=actor,
                    data={"terminal_status": "succeeded"},
                ),
            )
        elif decision == "mark_failed":
            mapped.append(
                _event_shell(
                    event_id=event_id_factory(),
                    event_type="goal.completed",
                    goal_id=goal_id,
                    time=timestamp,
                    source=source,
                    actor=actor,
                    data={"terminal_status": "failed"},
                ),
            )
        return mapped

    if update_type == "approval":
        decision = update["approval"]["decision"]
        event_type = {
            "requested": "approval.requested",
            "granted": "approval.granted",
            "denied": "approval.denied",
        }[decision]
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type=event_type,
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "recommendation_id": update.get("recommendation_id"),
                    "action_id": update.get("action_id"),
                    "approval": update["approval"],
                },
                invalidates=False if event_type == "approval.requested" else None,
            ),
        )
        return mapped

    if update_type == "question_answer":
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="question.answered",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "recommendation_id": update.get("recommendation_id"),
                    "question_answer": update["question_answer"],
                },
            ),
        )
        return mapped

    if update_type == "heartbeat":
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="executor.heartbeat",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={"heartbeat": update["heartbeat"]},
                invalidates=False,
            ),
        )
        return mapped

    if update_type == "policy_denied":
        mapped.append(
            _event_shell(
                event_id=event_id_factory(),
                event_type="executor.policy_denied",
                goal_id=goal_id,
                time=timestamp,
                source=source,
                actor=actor,
                data={
                    "recommendation_id": update.get("recommendation_id"),
                    "action_id": update.get("action_id"),
                    "policy_denied": update["policy_denied"],
                },
            ),
        )
        return mapped

    msg = f"unsupported update_type: {update_type}"
    raise InvalidInputError(msg)


def find_recommendation_by_issue(
    events: list[dict[str, Any]],
    *,
    issued_event_seq: int,
    issued_event_hash: str,
) -> dict[str, Any] | None:
    for event in events:
        if int(event["seq"]) != issued_event_seq:
            continue
        if event.get("event_hash") != issued_event_hash:
            msg = "issued_event_hash mismatch"
            raise StaleRecommendationError(msg)
        if event.get("type") == "recommendation.issued":
            data = event.get("data", {})
            if isinstance(data, dict):
                recommendation = data.get("recommendation")
                if isinstance(recommendation, dict):
                    return recommendation
        if event.get("type") == "recommendation.overridden":
            data = event.get("data", {})
            if isinstance(data, dict):
                override = data.get("override", {})
                replacement = data.get("replacement_recommendation")
                if (
                    isinstance(override, dict)
                    and override.get("decision") == "replace"
                    and isinstance(replacement, dict)
                ):
                    return replacement
    return None
