"""Human-readable narration for wayfinder-do (§8.1)."""

from __future__ import annotations

import sys
from typing import IO, Any


def _command_label(recommendation: dict[str, Any]) -> str:
    action = recommendation.get("action")
    if isinstance(action, dict):
        shell = action.get("shell")
        if isinstance(shell, dict):
            argv = shell.get("argv")
            if isinstance(argv, list) and argv:
                return " ".join(str(item) for item in argv)
        title = action.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    summary = recommendation.get("summary")
    if isinstance(summary, str):
        return summary
    return "recommendation"


def _action_result_suffix(action_result: dict[str, Any]) -> str:
    status = str(action_result.get("status", ""))
    process = action_result.get("process")
    if isinstance(process, dict):
        exit_code = process.get("exit_code")
        if exit_code is not None:
            return f"ran, exit {exit_code}"
    if status == "blocked":
        return "blocked"
    if action_result.get("changed") is True:
        return "ran, changed"
    return f"ran, {status or 'finished'}"


class NarratingReporter:
    """Print §8.1-style progress lines to stdout."""

    def __init__(self, *, stream: IO[str] | None = None) -> None:
        self._stream = stream or sys.stdout

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()

    def on_recommendation(self, recommendation: dict[str, Any]) -> None:
        rec_id = str(recommendation.get("recommendation_id", "rec"))
        rec_type = str(recommendation.get("recommendation_type", ""))
        if rec_type == "done":
            summary = recommendation.get("summary", "done")
            self._write(f"{rec_id}  done: {summary!r}")
            return
        label = _command_label(recommendation)
        self._write(f"{rec_id}  {label}")

    def on_action_result(
        self,
        recommendation: dict[str, Any],
        action_result: dict[str, Any],
    ) -> None:
        rec_id = str(recommendation.get("recommendation_id", "rec"))
        label = _command_label(recommendation)
        suffix = _action_result_suffix(action_result)
        self._write(f"{rec_id}  {label:<32} {suffix}")

    def on_goal_completed(self, recommendation: dict[str, Any]) -> None:
        del recommendation


def format_goal_created(goal: dict[str, Any]) -> str:
    """Format the goal-created line from a goal_create result."""
    goal_id = str(goal.get("goal_id", "goal"))
    workspace = goal.get("workspace_uri", "unknown")
    policy = goal.get("policy", {})
    risk = "none"
    if isinstance(policy, dict):
        risk = str(policy.get("max_auto_risk_level", "none"))
    return f"{goal_id} created  (workspace {workspace}, max auto risk: {risk})"


def format_goal_finished(goal_id: str, status: dict[str, Any]) -> str:
    """Format the final goal status line."""
    goal_status = str(status.get("goal_status", "unknown"))
    return f"{goal_id} {goal_status}"
