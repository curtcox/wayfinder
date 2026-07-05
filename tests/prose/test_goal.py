"""Prose goal composition tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import StubResponseQueue
from wayfinder.core.errors import SchemaValidationError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig
from wayfinder.prose.goal import compose_goal_create, generate_goal_create_draft


def test_compose_goal_create_resolves_relative_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    draft = {
        "description": "Make tests pass.",
        "workspace_path": str(workspace),
        "max_auto_risk_level": "low",
    }
    payload = compose_goal_create(draft, cwd=tmp_path)
    assert payload["schema"] == "wip.goal_create/0.1"
    assert payload["workspace_uri"] == f"file:{workspace.resolve()}"
    assert payload["policy"] == {"max_auto_risk_level": "low"}


def test_compose_goal_create_rejects_invalid_draft() -> None:
    with pytest.raises(SchemaValidationError, match="description"):
        compose_goal_create({"workspace_path": "/tmp"})


def test_generate_goal_create_draft_uses_stub(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    StubResponseQueue.items = [
        json.dumps(
            {
                "description": "Make the tests pass.",
                "workspace_path": str(workspace),
                "max_auto_risk_level": "low",
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    draft = generate_goal_create_draft(
        client,
        "Make the tests pass in this repo. Nothing above low risk.",
        cwd=tmp_path,
    )
    assert draft["description"] == "Make the tests pass."
    payload = compose_goal_create(draft, cwd=tmp_path)
    assert payload["workspace_uri"] == f"file:{workspace.resolve()}"
