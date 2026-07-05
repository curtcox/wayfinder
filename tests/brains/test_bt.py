"""Behavior-tree brain unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wayfinder.brains.bt import (
    BlackboardState,
    BtBrain,
    WaitOutcome,
    WaitSpec,
    _wait_elapsed,
    load_tree,
)
from wayfinder.core.errors import InvalidInputError


def _goal(workspace: Path) -> dict[str, object]:
    return {
        "goal_id": "goal_bt_01",
        "workspace_uri": f"file:{workspace}",
        "description": "Keep the staging environment healthy until further notice.",
        "metadata": {"reference_time": "2026-07-04T21:30:00Z"},
    }


def test_load_tree_parses_action_node(tmp_path: Path) -> None:
    tree_file = tmp_path / "simple.bt"
    tree_file.write_text(
        '{"root": {"type": "action", "name": "echo_ok", "title": "echo", "argv": ["echo", "ok"]}}',
        encoding="utf-8",
    )
    root = load_tree(tree_file)
    assert root.type == "action"
    assert root.action is not None
    assert root.action.argv == ("echo", "ok")


def test_bt_brain_issues_first_wait(tmp_path: Path) -> None:
    tree_file = Path(__file__).resolve().parents[2] / "examples" / "trees" / "staging-health.bt"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = BtBrain(tree_file)
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="summary",
    )
    assert recommendation["recommendation_type"] == "wait"
    assert "until_time" in recommendation["wait"]


def test_bt_brain_issues_probe_after_wait(tmp_path: Path) -> None:
    tree_file = Path(__file__).resolve().parents[2] / "examples" / "trees" / "staging-health.bt"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = BtBrain(tree_file)
    events = [
        {
            "type": "recommendation.issued",
            "data": {
                "recommendation": {
                    "recommendation_type": "wait",
                    "wait": {"until_time": "2026-07-04T21:10:00Z"},
                    "idempotency": {"key": "idem_bt_interval_wait_0"},
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
    assert recommendation["action"]["title"] == "probe /healthz"


def test_bt_brain_recovery_ladder_after_failed_probe(tmp_path: Path) -> None:
    tree_file = Path(__file__).resolve().parents[2] / "examples" / "trees" / "staging-health.bt"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = BtBrain(tree_file)
    events = [
        {
            "type": "recommendation.issued",
            "data": {
                "recommendation": {
                    "recommendation_type": "wait",
                    "wait": {"until_time": "2026-07-04T21:10:00Z"},
                    "idempotency": {"key": "idem_bt_interval_wait_0"},
                },
            },
        },
        {
            "type": "action.failed",
            "time": "2026-07-04T21:11:00Z",
            "data": {
                "action_result": {
                    "idempotency_key": "idem_bt_probe_healthz_0",
                    "shell": {"exit_code": 7},
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
    assert recommendation["action"]["title"] == "restart app service"


def test_bt_brain_simple_action_tree(tmp_path: Path) -> None:
    tree_file = Path(__file__).resolve().parents[2] / "examples" / "trees" / "test-check.bt"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    brain = BtBrain(tree_file)
    recommendation = brain.recommend(
        goal=_goal(workspace),
        status={"goal_status": "pending"},
        events=[],
        mode="preview",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "action"
    assert recommendation["action"]["shell"]["argv"] == ["echo", "ok"]


def test_blackboard_reference_time_parses_wait_elapsed() -> None:
    state = BlackboardState(
        waits=[
            WaitOutcome(
                node_name="interval_wait",
                until_time="2026-07-04T21:10:00Z",
                idempotency_key="idem_bt_interval_wait_0",
            ),
        ],
        reference_time=datetime(2026, 7, 4, 21, 15, tzinfo=UTC),
    )
    assert _wait_elapsed(
        state, WaitSpec(name="interval_wait", interval_seconds=900, summary="wait")
    )


def test_load_tree_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="not found"):
        load_tree(tmp_path / "missing.bt")
