"""LLM validation tests."""

from __future__ import annotations

import pytest

from wayfinder.core.errors import SchemaValidationError
from wayfinder.llm.validate import validate_brain_recommendation


def _minimal_action() -> dict[str, object]:
    return {
        "recommendation_type": "action",
        "summary": "Run true.",
        "goal_status": "running",
        "confidence": 0.8,
        "action": {
            "kind": "shell",
            "title": "Run true",
            "shell": {
                "argv": ["true"],
                "command_for_display": "true",
                "cwd": "file:/tmp",
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 30,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [],
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_test",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 1,
        },
        "risk": {
            "level": "low",
            "classes": ["execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
    }


def test_validate_brain_recommendation_accepts_minimal_action() -> None:
    validated = validate_brain_recommendation(_minimal_action())
    assert validated["recommendation_type"] == "action"


def test_validate_brain_recommendation_rejects_missing_risk() -> None:
    payload = _minimal_action()
    del payload["risk"]
    with pytest.raises(SchemaValidationError, match="risk"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_non_object() -> None:
    with pytest.raises(SchemaValidationError, match="JSON object"):
        validate_brain_recommendation("action")


def test_validate_brain_recommendation_rejects_unknown_type() -> None:
    payload = _minimal_action()
    payload["recommendation_type"] = "magic"
    with pytest.raises(SchemaValidationError, match="recommendation_type"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_empty_summary() -> None:
    payload = _minimal_action()
    payload["summary"] = "  "
    with pytest.raises(SchemaValidationError, match="summary"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_invalid_goal_status() -> None:
    payload = _minimal_action()
    payload["goal_status"] = "mystery"
    with pytest.raises(SchemaValidationError, match="goal_status"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_invalid_confidence() -> None:
    payload = _minimal_action()
    payload["confidence"] = 1.5
    with pytest.raises(SchemaValidationError, match="confidence"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_accepts_non_action_without_risk() -> None:
    validated = validate_brain_recommendation(
        {
            "recommendation_type": "done",
            "summary": "All done.",
            "goal_status": "succeeded",
            "confidence": 1.0,
        },
    )
    assert validated["recommendation_type"] == "done"


def test_validate_brain_recommendation_rejects_action_without_payload() -> None:
    payload = _minimal_action()
    del payload["action"]
    with pytest.raises(SchemaValidationError, match="action object"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_unsupported_action_kind() -> None:
    payload = _minimal_action()
    action = payload["action"]
    assert isinstance(action, dict)
    action["kind"] = "webhook"
    with pytest.raises(SchemaValidationError, match="unsupported action kind"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_accepts_noop_action() -> None:
    payload = _minimal_action()
    action = payload["action"]
    assert isinstance(action, dict)
    action["kind"] = "noop"
    validated = validate_brain_recommendation(payload)
    assert validated["action"]["kind"] == "noop"


def test_validate_brain_recommendation_rejects_bad_shell_argv() -> None:
    payload = _minimal_action()
    action = payload["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    shell["argv"] = []
    with pytest.raises(SchemaValidationError, match="shell.argv"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_missing_shell_cwd() -> None:
    payload = _minimal_action()
    action = payload["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    del shell["cwd"]
    with pytest.raises(SchemaValidationError, match="shell.cwd"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_bad_idempotency() -> None:
    payload = _minimal_action()
    payload["idempotency"] = "bad"
    with pytest.raises(SchemaValidationError, match="idempotency object"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_missing_idempotency_key() -> None:
    payload = _minimal_action()
    idempotency = payload["idempotency"]
    assert isinstance(idempotency, dict)
    del idempotency["key"]
    with pytest.raises(SchemaValidationError, match="idempotency.key"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_bad_risk_level() -> None:
    payload = _minimal_action()
    risk = payload["risk"]
    assert isinstance(risk, dict)
    risk["level"] = "extreme"
    with pytest.raises(SchemaValidationError, match="risk.level"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_empty_risk_classes() -> None:
    payload = _minimal_action()
    risk = payload["risk"]
    assert isinstance(risk, dict)
    risk["classes"] = []
    with pytest.raises(SchemaValidationError, match="risk.classes"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_bad_network_field() -> None:
    payload = _minimal_action()
    risk = payload["risk"]
    assert isinstance(risk, dict)
    risk["network"] = "maybe"
    with pytest.raises(SchemaValidationError, match="risk.network"):
        validate_brain_recommendation(payload)


def test_validate_brain_recommendation_rejects_bad_secrets_field() -> None:
    payload = _minimal_action()
    risk = payload["risk"]
    assert isinstance(risk, dict)
    risk["secrets"] = "maybe"
    with pytest.raises(SchemaValidationError, match="risk.secrets"):
        validate_brain_recommendation(payload)
