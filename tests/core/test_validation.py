"""Schema validation tests."""

from __future__ import annotations

import pytest

from wayfinder.core.errors import SchemaValidationError
from wayfinder.core.validation import validate


def test_validate_goal_schema() -> None:
    validate(
        {
            "schema": "wip.goal/0.1",
            "protocol_version": "0.1",
            "goal_id": "goal_01",
            "created_at": "2026-07-04T18:00:00Z",
            "actor": {"type": "human", "id": "curt", "authority": "owner"},
            "description": "test",
            "workspace_uri": "file:/tmp/project",
            "goal_status": "pending",
        },
        "wip.goal/0.1.json",
    )


def test_validate_rejects_bad_sha256_in_status() -> None:
    with pytest.raises(SchemaValidationError):
        validate(
            {
                "schema": "wip.status/0.1",
                "protocol_version": "0.1",
                "goal_id": "goal_01",
                "run_id": None,
                "observed_at": "2026-07-04T18:00:00Z",
                "goal_status": "pending",
                "reason_code": None,
                "progress": {
                    "summary": "",
                    "percent": None,
                    "completed_steps": 0,
                    "known_remaining_steps": None,
                },
                "last_issued_recommendation_id": None,
                "open_recommendation_id": None,
                "last_event_seq": 0,
                "event_log_head": "sha256:NOTVALID",
                "needs": [],
            },
            "wip.status/0.1.json",
        )
