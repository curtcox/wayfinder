"""PDDL planner brain unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.brains.plan import PlanBrain, _parse_init_facts, _progress_from_events
from wayfinder.core.errors import InvalidInputError

_DOMAIN = Path(__file__).resolve().parents[2] / "examples" / "domains" / "cluster-maintenance.pddl"

_PROBLEM = """(define (problem upgrade-node3)
  (:domain cluster-maintenance)
  (:objects n3 n4 - node)
  (:init (serving n3) (serving n4))
  (:goal (upgraded n3))
)"""

_BINDINGS = {
    "drain n3": {"argv": ["echo", "drain", "n3"], "title": "Drain node3"},
    "upgrade n3": {"argv": ["echo", "upgrade", "n3"], "title": "Upgrade node3 kernel 6.9"},
}


def _goal(workspace: Path) -> dict[str, object]:
    return {
        "goal_id": "goal_plan_01",
        "workspace_uri": f"file:{workspace}",
        "description": "Upgrade node3 to kernel 6.9 without losing cluster capacity.",
        "metadata": {
            "pddl_problem": _PROBLEM,
            "plan_actions": _BINDINGS,
        },
    }


def test_plan_brain_issues_first_step(tmp_path: Path) -> None:
    pytest.importorskip("pyperplan")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = PlanBrain(_DOMAIN)
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
    assert action["title"] == "Drain node3"
    explanation = recommendation["explanation"]
    assert isinstance(explanation, dict)
    evidence = explanation.get("evidence", [])
    assert isinstance(evidence, list)
    assert evidence[0]["status"] == "current"
    assert evidence[1]["status"] == "pending"


def test_plan_brain_issues_second_step_after_first_completes(tmp_path: Path) -> None:
    pytest.importorskip("pyperplan")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = PlanBrain(_DOMAIN)
    events = [
        {
            "type": "action.completed",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_plan_0_0",
                    "shell": {"exit_code": 0},
                },
            },
        },
    ]
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "running"},
        events=events,
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    assert action["title"] == "Upgrade node3 kernel 6.9"


def test_plan_brain_done_when_plan_complete(tmp_path: Path) -> None:
    pytest.importorskip("pyperplan")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = PlanBrain(_DOMAIN)
    events = [
        {
            "type": "action.completed",
            "data": {"action_result": {"idempotency_key": "idem_plan_0_0"}},
        },
        {
            "type": "action.completed",
            "data": {"action_result": {"idempotency_key": "idem_plan_0_1"}},
        },
    ]
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "running"},
        events=events,
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "done"


def test_progress_from_events_replans_after_failure() -> None:
    events = [
        {
            "type": "action.completed",
            "data": {"action_result": {"idempotency_key": "idem_plan_0_0"}},
        },
        {
            "type": "action.failed",
            "data": {"action_result": {"idempotency_key": "idem_plan_0_1"}},
        },
    ]
    progress = _progress_from_events(events)
    assert progress.completed_steps == 1
    assert progress.replan_requested is True


def test_parse_init_facts_extracts_predicates() -> None:
    facts = _parse_init_facts(_PROBLEM)
    assert "(serving n3)" in facts
    assert "(serving n4)" in facts


def test_plan_brain_requires_problem_without_llm(tmp_path: Path) -> None:
    pytest.importorskip("pyperplan")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    goal = {
        "goal_id": "goal_plan_02",
        "workspace_uri": f"file:{workspace}",
        "description": "No metadata problem here.",
    }
    brain = PlanBrain(_DOMAIN)
    with pytest.raises(InvalidInputError, match="pddl_problem"):
        brain.recommend(
            goal=goal,
            status={"goal_status": "pending"},
            events=[],
            mode="issue",
            explain_mode="none",
        )
