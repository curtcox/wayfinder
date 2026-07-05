"""Narration formatting tests."""

from __future__ import annotations

from wayfinder.prose.narrate import (
    NarratingReporter,
    format_goal_created,
    format_goal_finished,
)


def test_format_goal_created_includes_policy() -> None:
    line = format_goal_created(
        {
            "goal_id": "goal_01",
            "workspace_uri": "file:/tmp/project",
            "policy": {"max_auto_risk_level": "low"},
        },
    )
    assert "goal_01 created" in line
    assert "max auto risk: low" in line


def test_narrating_reporter_prints_action_result(capsys: object) -> None:
    reporter = NarratingReporter()
    recommendation = {
        "recommendation_id": "rec_01",
        "recommendation_type": "action",
        "action": {
            "shell": {
                "argv": ["make", "test"],
            },
        },
    }
    reporter.on_action_result(
        recommendation,
        {"status": "succeeded", "process": {"exit_code": 0}, "changed": False},
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "rec_01" in captured.out
    assert "make test" in captured.out
    assert "exit 0" in captured.out


def test_format_goal_finished() -> None:
    assert format_goal_finished("goal_01", {"goal_status": "succeeded"}) == "goal_01 succeeded"
