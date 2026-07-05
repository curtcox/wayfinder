"""LLM-backed recommendation brain."""

from __future__ import annotations

import json
from typing import Any

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig, load_llm_config
from wayfinder.llm.structured import generate_brain_recommendation


def _recent_events(events: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    if len(events) <= limit:
        return events
    return events[-limit:]


def _system_prompt() -> str:
    return (
        "You are a wayfinder brain for the Wayfinder Interaction Protocol (WIP v0.1). "
        "Given a goal, reduced status, and recent event history, emit exactly one next "
        "recommendation as JSON. Do not include issuance metadata such as recommendation_id, "
        "issued_at, lease, basis, or wayfinder fields. "
        "Allowed recommendation_type values: action, done, wait, question, blocked, unsafe. "
        "For action recommendations include action, idempotency, and risk objects. "
        "Shell actions must use kind=shell with argv, cwd (absolute file: URI), env, stdin, "
        "pty=false, timeout_seconds, expected_exit_codes, and requires_shell=false. "
        "Use honest risk metadata; network tools require network classes and "
        "requires_approval=true when network is required."
    )


class LLMBrain:
    """Generate recommendations via an OpenAI-compatible chat endpoint."""

    def __init__(self, client: ChatClient) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: LLMConfig | None = None) -> LLMBrain:
        resolved = config or load_llm_config()
        return cls(ChatClient(resolved))

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        del mode
        user_payload = {
            "goal": goal,
            "status": status,
            "recent_events": _recent_events(events),
            "explain_mode": explain_mode,
        }
        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        recommendation = generate_brain_recommendation(self._client, messages)
        if explain_mode == "none":
            recommendation.pop("explanation", None)
        return recommendation
