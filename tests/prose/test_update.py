"""Prose update composition tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, issue_recommendation, service_for_store
from tests.conftest import StubResponseQueue
from wayfinder.core.errors import InvalidInputError, SchemaValidationError
from wayfinder.exec.wayfinder_client import WayfinderClient
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig
from wayfinder.prose.context import gather_goal_context
from wayfinder.prose.update import (
    compose_update,
    format_update_receipt,
    generate_update_draft,
    validate_update_draft,
)


def test_validate_update_draft_rejects_unknown_type() -> None:
    with pytest.raises(SchemaValidationError, match="update_type"):
        validate_update_draft({"update_type": "heartbeat", "text": "x"})


def test_compose_observation_update(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issue_recommendation(service, goal_id)
    wf = WayfinderClient(command=[sys.executable, "-m", "wayfinder.cli"], store=str(store))
    context = gather_goal_context(wf, goal_id)
    update = compose_update(
        {
            "update_type": "observation",
            "text": "FYI, CI is red on main.",
            "invalidates": False,
        },
        context,
        update_id="upd_test_obs",
    )
    assert update["update_type"] == "observation"
    assert update["observations"][0]["effective"]["invalidates"] is False
    result = wf.update(goal_id, update)
    assert result["update_id"] == "upd_test_obs"
    receipt = format_update_receipt(update, result, context=context)
    assert "observation" in receipt
    assert "upd_test_obs" in receipt


def test_compose_question_answer_requires_open_question(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    wf = WayfinderClient(command=[sys.executable, "-m", "wayfinder.cli"], store=str(store))
    context = gather_goal_context(wf, goal_id)
    with pytest.raises(InvalidInputError, match="question"):
        compose_update({"update_type": "question_answer", "text": "pnpm"}, context)


def test_generate_update_draft_uses_stub(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    issue_recommendation(service, goal_id)
    wf = WayfinderClient(command=[sys.executable, "-m", "wayfinder.cli"], store=str(store))
    context = gather_goal_context(wf, goal_id)
    StubResponseQueue.items = [
        json.dumps(
            {
                "update_type": "observation",
                "text": "Context changed outside the loop.",
                "invalidates": True,
            },
        ),
    ]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    draft = generate_update_draft(client, "I changed package.json by hand.", context)
    assert draft["update_type"] == "observation"
    update = compose_update(draft, context)
    assert update["observations"][0]["effective"]["invalidates"] is True
