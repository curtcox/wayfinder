"""Web brain unit tests."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import StubResponseQueue
from wayfinder.brains.web import (
    WebBrain,
    _browser_history_from_events,
    _completed_step_count,
    _infer_risk_classes,
)
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig


def _goal(workspace: Path) -> dict[str, object]:
    return {
        "goal_id": "goal_web_01",
        "workspace_uri": f"file:{workspace}",
        "description": "Download June's invoice PDF from the vendor billing portal.",
        "metadata": {
            "web_steps": [
                {
                    "title": "Open billing portal",
                    "steps": [
                        {"op": "navigate", "url": "https://vendor.example/billing"},
                    ],
                    "risk_classes": ["network_read"],
                },
                {
                    "title": "Download June invoice",
                    "steps": [
                        {"op": "click", "selector": "a[data-invoice='june']"},
                        {"op": "await_download", "filename": "june.pdf"},
                    ],
                    "risk_classes": ["network_write", "external_side_effect"],
                },
            ],
        },
    }


def test_web_brain_issues_first_scripted_step(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = WebBrain()
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
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["x_browser_steps"] == [{"op": "navigate", "url": "https://vendor.example/billing"}]
    assert recommendation["idempotency"]["key"] == "idem_web_0"
    assert recommendation["risk"]["network"] == "required"


def test_web_brain_advances_after_action_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = WebBrain()
    events = [
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_web_0",
                    "status": "completed",
                    "shell": {"exit_code": 0},
                    "output": {"browser_transcript": "backend=stub"},
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
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "external_side_effect" in risk["classes"]
    assert recommendation["idempotency"]["key"] == "idem_web_1"


def test_web_brain_done_after_all_scripted_steps(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = WebBrain()
    events = [
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_web_0",
                    "status": "completed",
                    "output": {"browser_transcript": "ok"},
                },
            },
        },
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_web_1",
                    "status": "completed",
                    "output": {"browser_transcript": "ok"},
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


def test_browser_history_reconstructs_session() -> None:
    events = [
        {
            "type": "recommendation.issued",
            "data": {
                "recommendation": {
                    "recommendation_type": "action",
                    "idempotency": {"key": "idem_web_0"},
                    "action": {
                        "title": "Navigate",
                        "shell": {
                            "x_browser_steps": [{"op": "navigate", "url": "https://example.com"}],
                        },
                    },
                },
            },
        },
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_web_0",
                    "status": "completed",
                    "output": {"browser_transcript": "ok"},
                },
            },
        },
    ]
    history = _browser_history_from_events(events)
    assert len(history) == 1
    assert history[0].steps[0]["op"] == "navigate"
    assert _completed_step_count(history) == 1


def test_infer_risk_classes_side_effect_on_click() -> None:
    assert "external_side_effect" in _infer_risk_classes(
        [{"op": "navigate", "url": "https://x"}, {"op": "click", "selector": "button"}],
    )


def test_web_brain_llm_path_returns_action(stub_server: str, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent_response = {
        "decision": "run_browser",
        "steps": [{"op": "navigate", "url": "https://vendor.example/billing"}],
        "title": "Open billing portal",
        "summary": "Navigate to the vendor billing page.",
        "reasoning": "Start by loading the portal.",
    }
    StubResponseQueue.items = [json.dumps(agent_response)]
    client = ChatClient(
        LLMConfig(base_url=stub_server, api_key="test-key", model="test-model"),
    )
    brain = WebBrain(llm_client=client)
    recommendation = brain.recommend(
        goal={
            "goal_id": "goal_web_llm",
            "workspace_uri": f"file:{workspace}",
            "description": "Download invoice.",
        },
        status={"goal_status": "pending"},
        events=[],
        mode="preview",
        explain_mode="structured",
    )
    assert recommendation["recommendation_type"] == "action"
    assert recommendation["executable"] is False
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["x_browser_steps"][0]["op"] == "navigate"
