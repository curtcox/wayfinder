"""OpenAI-compatible chat-completions client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from wayfinder.llm.config import LLMConfig
from wayfinder.llm.errors import LLMError


class ChatClient:
    """Minimal chat-completions client for structured recommendation generation."""

    def __init__(self, config: LLMConfig, *, timeout_seconds: float = 120.0) -> None:
        self._config = config
        self._timeout = timeout_seconds

    @property
    def config(self) -> LLMConfig:
        return self._config

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = True,
    ) -> str:
        """Return the assistant message content from a chat completion."""
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._config.base_url}/chat/completions"
        try:
            response = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"LLM request failed: {exc}"
            raise LLMError(msg) from exc

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            msg = "LLM response missing assistant content"
            raise LLMError(msg) from exc
        if not isinstance(content, str) or not content.strip():
            msg = "LLM response content was empty"
            raise LLMError(msg)
        return content
