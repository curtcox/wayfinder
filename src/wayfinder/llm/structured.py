"""Structured JSON generation with schema validation and retries."""

from __future__ import annotations

import json
from typing import Any

from wayfinder.core.errors import SchemaValidationError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError
from wayfinder.llm.validate import validate_brain_recommendation


def generate_brain_recommendation(
    client: ChatClient,
    messages: list[dict[str, str]],
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call the LLM and return a validated brain recommendation draft."""
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
                return validate_brain_recommendation(parsed)
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
    msg = f"LLM failed to produce valid recommendation after {max_retries} attempts: {last_error}"
    raise LLMError(msg)
