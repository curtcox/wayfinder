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
