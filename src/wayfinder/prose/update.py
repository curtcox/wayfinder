"""Compose wip.update documents from natural-language intent."""

from __future__ import annotations

import getpass
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from wayfinder.core.errors import InvalidInputError, SchemaValidationError
from wayfinder.core.validation import validate
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError
from wayfinder.prose.context import GoalContext

UPDATE_SCHEMA = "wip.update/0.1.json"
ALLOWED_UPDATE_TYPES = frozenset(
    {
        "observation",
        "correction",
        "question_answer",
        "approval",
        "override",
        "goal_cancel",
    },
)


def _update_system_prompt() -> str:
    return (
        "You classify user prose into a Wayfinder update draft. Return only JSON with keys: "
        "update_type (one of observation, correction, question_answer, approval, override, "
        "goal_cancel), text (the user's substance as a string), invalidates (boolean, for "
        "observation/correction when the open recommendation should be invalidated), "
        "recommendation_id (when approving/rejecting/overriding a specific recommendation), "
        "approval_decision (granted or denied, for approval), override_decision (replace or "
        "mark_done or mark_failed or mark_blocked, for override), replacement_argv (string "
        "array for override replace), replacement_title (string for override replace). "
        "Do not include protocol envelope fields."
    )


def validate_update_draft(draft: Any) -> dict[str, Any]:
    """Validate an LLM-produced update draft."""
    if not isinstance(draft, dict):
        msg = "update draft must be a JSON object"
        raise SchemaValidationError(msg)
    update_type = draft.get("update_type")
    if update_type not in ALLOWED_UPDATE_TYPES:
        msg = f"invalid update_type: {update_type!r}"
        raise SchemaValidationError(msg)
    text = draft.get("text")
    if not isinstance(text, str) or not text.strip():
        msg = "text must be a non-empty string"
        raise SchemaValidationError(msg)
    return draft


def _default_actor() -> dict[str, Any]:
    return {
        "type": "human",
        "id": getpass.getuser(),
        "authority": "owner",
    }


def _workspace_uri(context: GoalContext) -> str:
    uri = context.goal.get("workspace_uri")
    if isinstance(uri, str) and uri:
        return uri
    msg = "goal missing workspace_uri"
    raise InvalidInputError(msg)


def _build_replacement_recommendation(
    context: GoalContext,
    *,
    argv: list[str],
    title: str,
    reason: str,
) -> dict[str, Any]:
    open_rec = context.open_recommendation
    issue_event = context.open_issue_event
    if open_rec is None or issue_event is None:
        msg = "override.replace requires an open recommendation"
        raise InvalidInputError(msg)
    workspace = _workspace_uri(context)
    rec_id = f"rec_{secrets.token_hex(4)}"
    action_id = f"act_{secrets.token_hex(4)}"
    issued_seq = int(issue_event["seq"])
    issued_hash = str(issue_event["event_hash"])
    display = " ".join(argv)
    return {
        "schema": "wip.recommendation/0.1",
        "protocol_version": "0.1",
        "goal_id": context.goal_id,
        "recommendation_id": rec_id,
        "recommendation_type": "action",
        "summary": title,
        "goal_status": "running",
        "confidence": 0.9,
        "executable": True,
        "issued_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": "2099-01-01T00:00:00Z",
        "parallel": False,
        "supersedes": [str(open_rec["recommendation_id"])],
        "wayfinder": {"name": "human", "version": "0.1", "instance_id": "override"},
        "basis": {
            "event_log_seq": issued_seq,
            "event_log_head": issued_hash,
            "state_version": "override",
        },
        "lease": {
            "lease_id": f"lease_{secrets.token_hex(4)}",
            "lease_expires_at": "2099-01-01T00:00:00Z",
        },
        "action": {
            "action_id": action_id,
            "kind": "shell",
            "title": title,
            "shell": {
                "argv": argv,
                "command_for_display": display,
                "cwd": workspace,
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 600,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [],
        },
        "idempotency": {
            "level": "strong",
            "key": f"idem_{secrets.token_hex(4)}",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 2,
        },
        "risk": {
            "level": "low",
            "classes": ["read_local", "execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "run_id": None,
        "explanation": {"mode": "summary", "summary": reason, "evidence": [], "redactions": []},
    }


def compose_update(
    draft: dict[str, Any],
    context: GoalContext,
    *,
    update_id: str | None = None,
) -> dict[str, Any]:
    """Turn a validated draft into a wip.update document."""
    validated = validate_update_draft(draft)
    update_type = str(validated["update_type"])
    text = validated["text"].strip()
    payload: dict[str, Any] = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": update_id or f"upd_{secrets.token_hex(4)}",
        "goal_id": context.goal_id,
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": _default_actor(),
        "update_type": update_type,
    }

    open_rec = context.open_recommendation
    open_id = open_rec.get("recommendation_id") if open_rec else None
    draft_rec_id = validated.get("recommendation_id")
    target_rec_id = draft_rec_id if isinstance(draft_rec_id, str) and draft_rec_id else open_id

    if update_type == "observation":
        invalidates = validated.get("invalidates", True)
        if not isinstance(invalidates, bool):
            invalidates = True
        payload["observations"] = [
            {"text": text, "effective": {"invalidates": invalidates}},
        ]
    elif update_type == "correction":
        invalidates = validated.get("invalidates", True)
        if not isinstance(invalidates, bool):
            invalidates = True
        payload["correction"] = {"text": text, "effective": {"invalidates": invalidates}}
    elif update_type == "question_answer":
        if open_rec is None or open_rec.get("recommendation_type") != "question":
            msg = "question_answer requires an open question recommendation"
            raise InvalidInputError(msg)
        question = open_rec.get("question")
        if not isinstance(question, dict):
            msg = "open question recommendation missing question payload"
            raise InvalidInputError(msg)
        question_id = question.get("question_id")
        if not isinstance(question_id, str):
            msg = "open question missing question_id"
            raise InvalidInputError(msg)
        payload["recommendation_id"] = str(open_rec["recommendation_id"])
        payload["question_answer"] = {"question_id": question_id, "answer": text}
    elif update_type == "approval":
        if not isinstance(target_rec_id, str):
            msg = "approval requires a recommendation_id"
            raise InvalidInputError(msg)
        decision = validated.get("approval_decision", "granted")
        if decision not in {"granted", "denied"}:
            decision = "granted"
        payload["recommendation_id"] = target_rec_id
        action = open_rec.get("action") if open_rec else None
        if isinstance(action, dict) and action.get("action_id"):
            payload["action_id"] = str(action["action_id"])
        payload["approval"] = {"decision": decision, "reason": text}
    elif update_type == "override":
        if not isinstance(target_rec_id, str):
            msg = "override requires a recommendation_id"
            raise InvalidInputError(msg)
        payload["recommendation_id"] = target_rec_id
        override_decision = validated.get("override_decision", "replace")
        if override_decision == "replace":
            argv = validated.get("replacement_argv")
            if not isinstance(argv, list) or not argv:
                msg = "override.replace requires replacement_argv"
                raise InvalidInputError(msg)
            title = validated.get("replacement_title")
            if not isinstance(title, str) or not title.strip():
                title = " ".join(str(item) for item in argv)
            replacement = _build_replacement_recommendation(
                context,
                argv=[str(item) for item in argv],
                title=title.strip(),
                reason=text,
            )
            payload["override"] = {
                "decision": "replace",
                "reason": text,
                "replacement_recommendation": replacement,
            }
        else:
            if override_decision not in {
                "mark_done",
                "mark_failed",
                "mark_blocked",
            }:
                override_decision = "mark_done"
            payload["override"] = {"decision": override_decision, "reason": text}
    elif update_type == "goal_cancel":
        payload["goal_cancel"] = {"reason": text}

    validate(payload, UPDATE_SCHEMA)
    return payload


def format_update_receipt(
    update: dict[str, Any],
    _result: dict[str, Any],
    *,
    context: GoalContext,
) -> str:
    """Format the one-line receipt printed after a tell submission."""
    update_id = str(update.get("update_id", "upd"))
    update_type = str(update.get("update_type", "update"))
    open_id = context.status.get("open_recommendation_id")
    invalidated = update_type in {"observation", "correction"} and any(
        isinstance(obs, dict) and obs.get("effective", {}).get("invalidates") is True
        for obs in update.get("observations", [])
    )
    if update_type == "correction":
        correction = update.get("correction")
        if isinstance(correction, dict):
            effective = correction.get("effective")
            if isinstance(effective, dict):
                invalidated = effective.get("invalidates") is True
    suffix = ""
    if invalidated and open_id:
        suffix = f" (invalidates {open_id}; the wayfinder will rethink)"
    elif update_type == "approval":
        decision = update.get("approval", {}).get("decision", "granted")
        rec_id = update.get("recommendation_id", "recommendation")
        suffix = f" on {rec_id} ({decision})"
    elif update_type == "question_answer":
        suffix = ""
    return f"recorded {update_type} {update_id}{suffix}"


def generate_update_draft(
    client: ChatClient,
    prose: str,
    context: GoalContext,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call the LLM to classify prose into an update draft."""
    context_payload = {
        "goal_id": context.goal_id,
        "goal_status": context.status.get("goal_status"),
        "open_recommendation_id": context.status.get("open_recommendation_id"),
        "open_recommendation": context.open_recommendation,
        "recent_events": context.recent_events[-10:],
        "user_prose": prose,
    }
    messages = [
        {"role": "system", "content": _update_system_prompt()},
        {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
    ]
    conversation = list(messages)
    last_error = "unknown validation error"
    for _attempt in range(max_retries):
        content = client.complete(conversation, json_mode=True)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            last_error = f"response was not valid JSON: {exc}"
        else:
            try:
                return validate_update_draft(parsed)
            except SchemaValidationError as exc:
                last_error = str(exc)
        conversation = [
            *conversation,
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "Your previous JSON failed validation: "
                    f"{last_error}. Return corrected JSON only."
                ),
            },
        ]
    msg = f"LLM failed to produce valid update draft after {max_retries} attempts: {last_error}"
    raise LLMError(msg)
