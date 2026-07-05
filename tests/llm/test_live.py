"""Live LLM smoke tests (skipped without API configuration)."""

from __future__ import annotations

import os

import pytest

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config
from wayfinder.llm.structured import generate_brain_recommendation

pytestmark = pytest.mark.live


def _llm_configured() -> bool:
    return all(
        os.environ.get(key)
        for key in ("WAYFINDER_LLM_BASE_URL", "WAYFINDER_LLM_API_KEY", "WAYFINDER_LLM_MODEL")
    )


@pytest.mark.skipif(not _llm_configured(), reason="WAYFINDER_LLM_* environment not configured")
def test_live_chat_completion_smoke() -> None:
    config = load_llm_config()
    client = ChatClient(config)
    result = generate_brain_recommendation(
        client,
        [
            {
                "role": "user",
                "content": (
                    'Recommend running "true" as the next shell action for a trivial goal.'
                ),
            },
        ],
        max_retries=2,
    )
    assert result["recommendation_type"] == "action"
