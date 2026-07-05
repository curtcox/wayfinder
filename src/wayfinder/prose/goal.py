"""Compose wip.goal_create documents from natural-language intent."""

from __future__ import annotations

import getpass
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.core.errors import SchemaValidationError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError

ALLOWED_RISK_LEVELS = frozenset({"none", "low", "medium", "high", "critical"})


def _goal_system_prompt() -> str:
    return (
        "You translate user intent into a JSON object for creating a Wayfinder goal. "
        "Return only JSON with keys: description (string), workspace_path (absolute "
        "filesystem path for the working directory), and optional max_auto_risk_level "
        "(one of none, low, medium, high, critical when the user states a hard risk limit). "
        "Do not include protocol envelope fields."
    )


def validate_goal_draft(draft: Any) -> dict[str, Any]:
    """Validate an LLM-produced goal draft before envelope composition."""
    if not isinstance(draft, dict):
        msg = "goal draft must be a JSON object"
        raise SchemaValidationError(msg)
    description = draft.get("description")
    if not isinstance(description, str) or not description.strip():
        msg = "description must be a non-empty string"
        raise SchemaValidationError(msg)
    workspace_path = draft.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path.strip():
        msg = "workspace_path must be a non-empty string"
        raise SchemaValidationError(msg)
    risk_level = draft.get("max_auto_risk_level")
    if risk_level is not None and risk_level not in ALLOWED_RISK_LEVELS:
        msg = f"invalid max_auto_risk_level: {risk_level!r}"
        raise SchemaValidationError(msg)
    return draft


def _resolve_workspace_uri(workspace_path: str, *, cwd: Path) -> str:
    path = Path(workspace_path).expanduser()
    path = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
    return f"file:{path}"


def compose_goal_create(
    draft: dict[str, Any],
    *,
    cwd: Path | None = None,
    create_id: str | None = None,
) -> dict[str, Any]:
    """Turn a validated draft into a wip.goal_create document."""
    validated = validate_goal_draft(draft)
    working_dir = cwd or Path.cwd()
    payload: dict[str, Any] = {
        "schema": "wip.goal_create/0.1",
        "protocol_version": "0.1",
        "create_id": create_id or f"create_{secrets.token_hex(4)}",
        "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": {
            "type": "human",
            "id": getpass.getuser(),
            "authority": "owner",
        },
        "description": validated["description"].strip(),
        "workspace_uri": _resolve_workspace_uri(str(validated["workspace_path"]), cwd=working_dir),
    }
    risk_level = validated.get("max_auto_risk_level")
    if isinstance(risk_level, str):
        payload["policy"] = {"max_auto_risk_level": risk_level}
    return payload


def generate_goal_create_draft(
    client: ChatClient,
    prose: str,
    *,
    cwd: Path | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call the LLM to derive a goal draft from prose."""
    working_dir = cwd or Path.cwd()
    user_payload = {
        "intent": prose,
        "cwd": str(working_dir.resolve()),
    }
    messages = [
        {"role": "system", "content": _goal_system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
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
                return validate_goal_draft(parsed)
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
    msg = f"LLM failed to produce valid goal draft after {max_retries} attempts: {last_error}"
    raise LLMError(msg)
