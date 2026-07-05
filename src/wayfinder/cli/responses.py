"""CLI response envelopes and error mapping (§1.2–§1.3)."""

from __future__ import annotations

import json
import sys
from typing import Any

from wayfinder.core.errors import (
    ArtifactIntegrityError,
    InvalidInputError,
    PolicyDeniedError,
    SchemaValidationError,
    StaleRecommendationError,
    StorageConflictError,
)
from wayfinder.core.hash_chain import CorruptEventLogError
from wayfinder.llm.errors import LLMConfigError, LLMError

PROTOCOL_VERSION = "0.1"

ERROR_EXIT_CODES: dict[str, int] = {
    "invalid_input": 1,
    "storage_conflict": 2,
    "temporary_failure": 3,
    "unsupported_capability": 4,
    "stale_recommendation": 5,
    "internal_error": 6,
    "policy_denied": 7,
    "corrupt_event_log": 8,
    "artifact_integrity_failed": 9,
}


def success_response(
    command: str,
    result: dict[str, Any],
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "schema": "wip.response/0.1",
        "protocol_version": PROTOCOL_VERSION,
        "command": command,
        "result": result,
    }
    if request_id is not None:
        envelope["request_id"] = request_id
    return envelope


def error_object(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    event_log_head: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "wip.error/0.1",
        "protocol_version": PROTOCOL_VERSION,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "retry_after_seconds": retry_after_seconds,
            "event_log_head": event_log_head,
            "details": details or {},
        },
    }
    if request_id is not None:
        payload["request_id"] = request_id
    return payload


def exit_code_for_error(code: str) -> int:
    return ERROR_EXIT_CODES.get(code, 6)


def map_exception(
    exc: BaseException,
    *,
    request_id: str | None = None,
    event_log_head: str | None = None,
) -> tuple[dict[str, Any], int]:
    if isinstance(exc, InvalidInputError):
        code = "invalid_input"
    elif isinstance(exc, (SchemaValidationError, LLMConfigError, LLMError)):
        code = "invalid_input"
    elif isinstance(exc, StorageConflictError):
        code = "storage_conflict"
    elif isinstance(exc, StaleRecommendationError):
        code = "stale_recommendation"
    elif isinstance(exc, PolicyDeniedError):
        code = "policy_denied"
    elif isinstance(exc, CorruptEventLogError):
        code = "corrupt_event_log"
    elif isinstance(exc, ArtifactIntegrityError):
        code = "artifact_integrity_failed"
    else:
        code = "internal_error"
    return (
        error_object(
            code,
            str(exc),
            request_id=request_id,
            event_log_head=event_log_head,
        ),
        exit_code_for_error(code),
    )


def write_json(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def write_jsonl_lines(lines: list[str]) -> None:
    for line in lines:
        sys.stdout.write(line)
    sys.stdout.flush()
