"""Codex brain unit tests."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import StubResponseQueue
from wayfinder.brains.codex import (
    CodexBrain,
    _completed_step_count,
    _tool_history_from_events,
)
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


def _goal(workspace: Path) -> dict[str, object]:
    return {
        "goal_id": "goal_codex_01",
        "workspace_uri": f"file:{workspace}",
        "description": "Find and fix the memory leak the soak test keeps hitting.",
        "metadata": {
            "codex_steps": [
                {"argv": ["grep", "-r", "malloc", "."], "title": "Search for malloc usage"},
                {"argv": ["true"], "title": "Verify fix"},
            ],
        },
    }


def test_codex_brain_issues_first_scripted_step(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = CodexBrain()
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="structured",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    assert action["shell"]["argv"] == ["grep", "-r", "malloc", "."]
    assert recommendation["idempotency"]["key"] == "idem_codex_0"


def test_codex_brain_advances_after_action_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = CodexBrain()
    events = [
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_codex_0",
                    "status": "completed",
                    "shell": {"exit_code": 0},
                    "output": {"stdout": "src/leak.c:42", "stderr": ""},
                },
            },
        },
    ]
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "running"},
        events=events,
        mode="issue",
        explain_mode="structured",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    assert action["shell"]["argv"] == ["true"]
    assert recommendation["idempotency"]["key"] == "idem_codex_1"


def test_codex_brain_done_after_all_scripted_steps(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = CodexBrain()
    events = [
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_codex_0",
                    "status": "completed",
                    "shell": {"exit_code": 0},
                    "output": {"stdout": "", "stderr": ""},
                },
            },
        },
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_codex_1",
                    "status": "completed",
                    "shell": {"exit_code": 0},
                    "output": {"stdout": "", "stderr": ""},
                },
            },
        },
    ]
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "running"},
        events=events,
        mode="issue",
        explain_mode="structured",
    )
    assert recommendation["recommendation_type"] == "done"


def test_tool_history_reconstructs_agent_session() -> None:
    events = [
        {
            "type": "recommendation.issued",
            "data": {
                "recommendation": {
                    "recommendation_type": "action",
                    "idempotency": {"key": "idem_codex_0"},
                    "action": {
                        "title": "Search",
                        "shell": {"argv": ["grep", "-r", "malloc", "."]},
                    },
                },
            },
        },
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_codex_0",
                    "status": "completed",
                    "shell": {"exit_code": 0},
                    "output": {"stdout": "hit", "stderr": ""},
                },
            },
        },
    ]
    history = _tool_history_from_events(events)
    assert len(history) == 1
    assert history[0].argv == ["grep", "-r", "malloc", "."]
    assert history[0].stdout == "hit"
    assert _completed_step_count(history) == 1


def test_codex_brain_llm_path_returns_action(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_response = {
        "decision": "run_shell",
        "argv": ["rg", "malloc"],
        "title": "Search for malloc",
        "summary": "Ripgrep for malloc references.",
        "reasoning": "Start with a fast workspace search.",
    }
    StubResponseQueue.items = [json.dumps(agent_response)]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    brain = CodexBrain(llm_client=client)
    recommendation = brain.recommend(
        goal={
            "goal_id": "goal_codex_llm",
            "workspace_uri": f"file:{workspace}",
            "description": "Find malloc usage.",
        },
        status={"goal_status": "pending"},
        events=[],
        mode="preview",
        explain_mode="structured",
    )
    assert recommendation["recommendation_type"] == "action"
    assert recommendation["executable"] is False
    explanation = recommendation["explanation"]
    assert isinstance(explanation, dict)
    evidence = explanation.get("evidence", [])
    assert isinstance(evidence, list)
    assert evidence[0]["kind"] == "agent_reasoning"
