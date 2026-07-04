"""Capabilities object for wayfinder (§12)."""

from __future__ import annotations

from typing import Any

from wayfinder import __version__


def build_capabilities(*, instance_id: str = "wayfinder_local") -> dict[str, Any]:
    return {
        "schema": "wip.capabilities/0.1",
        "protocol_version": "0.1",
        "protocol_versions": ["0.1"],
        "wayfinder": {
            "name": "wayfinder",
            "version": __version__,
            "instance_id": instance_id,
        },
        "transports": ["cli"],
        "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
        "recommendation_types": ["action", "question", "wait", "blocked", "done", "unsafe"],
        "action_kinds": ["shell", "noop"],
        "precondition_kinds": ["path_exists", "command_available", "env_present", "approval"],
        "success_criteria_kinds": ["exit_code", "artifact_exists", "observation_recorded"],
        "update_types": [
            "recommendation_disposition",
            "action_started",
            "action_result",
            "observation",
            "correction",
            "redaction",
            "override",
            "question_answer",
            "approval",
            "heartbeat",
            "policy_denied",
            "goal_cancel",
        ],
        "event_types": [
            "goal.created",
            "goal.cancelled",
            "goal.completed",
            "recommendation.issued",
            "recommendation.superseded",
            "recommendation.accepted",
            "recommendation.rejected",
            "recommendation.overridden",
            "recommendation.expired",
            "action.started",
            "action.completed",
            "action.failed",
            "action.blocked",
            "action.skipped",
            "action.cancelled",
            "action.timed_out",
            "action.output_recorded",
            "observation.recorded",
            "correction.recorded",
            "redaction.recorded",
            "approval.requested",
            "approval.granted",
            "approval.denied",
            "question.answered",
            "executor.heartbeat",
            "executor.policy_denied",
        ],
        "explanation_modes": ["none", "summary", "structured", "debug"],
        "dry_run_modes": ["preview", "issue"],
        "features": {
            "supersede": True,
            "verify": True,
            "cancellation": False,
            "pty": False,
        },
        "event_log": {
            "format": "jsonl",
            "hash_chain": True,
            "history_query": True,
            "canonicalization": "RFC8785",
        },
        "limits": {
            "max_inline_output_bytes": 8192,
            "max_inline_stdin_bytes": 8192,
            "max_recommendation_bytes": 1048576,
            "max_artifact_bytes": 104857600,
            "max_history_events_per_page": 1000,
        },
        "extensions": {
            "namespaces": [],
        },
    }
