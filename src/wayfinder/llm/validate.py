"""Validate brain recommendation drafts before service finalization."""

from __future__ import annotations

from typing import Any

from wayfinder.core.errors import SchemaValidationError

ALLOWED_RECOMMENDATION_TYPES = frozenset(
    {"action", "done", "wait", "question", "blocked", "unsafe"},
)
ALLOWED_GOAL_STATUSES = frozenset(
    {"pending", "running", "waiting", "blocked", "succeeded", "failed", "cancelled"},
)
ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
ALLOWED_NETWORK = frozenset({"not_required", "required", "unknown"})
ALLOWED_SECRETS = frozenset({"not_required", "required", "unknown"})


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{field} must be a non-empty string"
        raise SchemaValidationError(msg)
    return value


def _validate_shell_action(action: dict[str, Any]) -> None:
    kind = action.get("kind")
    if kind not in {"shell", "noop"}:
        msg = f"unsupported action kind: {kind!r}"
        raise SchemaValidationError(msg)
    if kind != "shell":
        return
    shell = action.get("shell")
    if not isinstance(shell, dict):
        msg = "shell action requires shell object"
        raise SchemaValidationError(msg)
    argv = shell.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        msg = "shell.argv must be a non-empty string array"
        raise SchemaValidationError(msg)
    if not isinstance(shell.get("cwd"), str):
        msg = "shell.cwd must be a string"
        raise SchemaValidationError(msg)


def _validate_idempotency(idempotency: object) -> None:
    if not isinstance(idempotency, dict):
        msg = "action recommendation requires idempotency object"
        raise SchemaValidationError(msg)
    if not isinstance(idempotency.get("key"), str):
        msg = "idempotency.key must be a string"
        raise SchemaValidationError(msg)


def _validate_risk(risk: object) -> None:
    if not isinstance(risk, dict):
        msg = "action recommendation requires risk object"
        raise SchemaValidationError(msg)
    if risk.get("level") not in ALLOWED_RISK_LEVELS:
        msg = f"invalid risk.level: {risk.get('level')!r}"
        raise SchemaValidationError(msg)
    classes = risk.get("classes")
    if not isinstance(classes, list) or not classes:
        msg = "risk.classes must be a non-empty array"
        raise SchemaValidationError(msg)
    if risk.get("network") not in ALLOWED_NETWORK:
        msg = "risk.network must be not_required, required, or unknown"
        raise SchemaValidationError(msg)
    if risk.get("secrets") not in ALLOWED_SECRETS:
        msg = "risk.secrets must be not_required, required, or unknown"
        raise SchemaValidationError(msg)


def validate_brain_recommendation(recommendation: Any) -> dict[str, Any]:
    """Validate a brain-produced recommendation draft."""
    if not isinstance(recommendation, dict):
        msg = "recommendation must be a JSON object"
        raise SchemaValidationError(msg)

    rec_type = recommendation.get("recommendation_type")
    if rec_type not in ALLOWED_RECOMMENDATION_TYPES:
        msg = f"invalid recommendation_type: {rec_type!r}"
        raise SchemaValidationError(msg)

    _require_string(recommendation.get("summary"), field="summary")

    goal_status = recommendation.get("goal_status")
    if goal_status not in ALLOWED_GOAL_STATUSES:
        msg = f"invalid goal_status: {goal_status!r}"
        raise SchemaValidationError(msg)

    confidence = recommendation.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        msg = "confidence must be a number between 0 and 1"
        raise SchemaValidationError(msg)

    if rec_type == "action":
        action = recommendation.get("action")
        if not isinstance(action, dict):
            msg = "action recommendation requires action object"
            raise SchemaValidationError(msg)
        _validate_shell_action(action)
        _validate_idempotency(recommendation.get("idempotency"))
        _validate_risk(recommendation.get("risk"))

    return recommendation
