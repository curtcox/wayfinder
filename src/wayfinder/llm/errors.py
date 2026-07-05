"""LLM client errors."""

from __future__ import annotations


class LLMError(Exception):
    """An LLM request or response could not be processed."""


class LLMConfigError(LLMError):
    """LLM configuration is missing or invalid."""
