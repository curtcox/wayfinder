"""ChatClient unit tests."""

from __future__ import annotations

import pytest

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig
from wayfinder.llm.errors import LLMError


def test_chat_client_config_property() -> None:
    config = LLMConfig(base_url="http://127.0.0.1:9/v1", api_key="k", model="m")
    assert ChatClient(config).config is config


def test_complete_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def broken_post(*_args, **_kwargs):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr("wayfinder.llm.client.httpx.post", broken_post)
    client = ChatClient(LLMConfig(base_url="http://127.0.0.1:9/v1", api_key="k", model="m"))
    with pytest.raises(LLMError, match="LLM request failed"):
        client.complete([{"role": "user", "content": "hi"}], json_mode=False)


def test_complete_raises_on_missing_content(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {}}]}

    monkeypatch.setattr("wayfinder.llm.client.httpx.post", lambda *_a, **_k: FakeResponse())
    client = ChatClient(LLMConfig(base_url="http://127.0.0.1:9/v1", api_key="k", model="m"))
    with pytest.raises(LLMError, match="missing assistant content"):
        client.complete([{"role": "user", "content": "hi"}])
