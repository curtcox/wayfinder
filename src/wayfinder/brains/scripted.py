"""Deterministic scripted brain driven by a JSON playbook."""

from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path
from typing import Any

from wayfinder.core.errors import InvalidInputError


def _load_default_playbook() -> dict[str, Any]:
    data = (
        resources.files("wayfinder.brains")
        .joinpath("default_playbook.json")
        .read_text(
            encoding="utf-8",
        )
    )
    parsed: dict[str, Any] = json.loads(data)
    return parsed


def _match_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if "$gte" in expected and not (isinstance(actual, int) and actual >= expected["$gte"]):
            return False
        if "$lte" in expected and not (isinstance(actual, int) and actual <= expected["$lte"]):
            return False
        if "$null" in expected:
            return (actual is None) == bool(expected["$null"])
        return False
    return bool(actual == expected)


def _status_facts(status: dict[str, Any]) -> dict[str, Any]:
    facts = dict(status)
    progress = status.get("progress")
    if isinstance(progress, dict) and "completed_steps" in progress:
        facts["completed_steps"] = progress["completed_steps"]
    return facts


def _rule_matches(rule: dict[str, Any], *, status: dict[str, Any], goal: dict[str, Any]) -> bool:
    match = rule.get("match", {})
    if not isinstance(match, dict):
        return False
    facts = _status_facts(status)
    for key, expected in match.items():
        if key == "description_contains":
            description = str(goal.get("description", ""))
            if str(expected) not in description:
                return False
            continue
        actual = facts.get(key)
        if not _match_value(actual, expected):
            return False
    return True


def _substitute(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        out = value
        for name, replacement in variables.items():
            out = out.replace(f"{{{name}}}", replacement)
        return out
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    return value


class ScriptedBrain:
    """State-to-recommendation mapping from an ordered rule list."""

    def __init__(self, playbook: dict[str, Any]) -> None:
        rules = playbook.get("rules")
        if not isinstance(rules, list) or not rules:
            msg = "scripted brain playbook requires a non-empty rules array"
            raise InvalidInputError(msg)
        self._rules = rules

    @classmethod
    def from_path(cls, path: Path) -> ScriptedBrain:
        parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(parsed)

    @classmethod
    def default(cls) -> ScriptedBrain:
        return cls(_load_default_playbook())

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        del events, mode
        variables = {
            "goal_id": str(goal["goal_id"]),
            "workspace_uri": str(goal["workspace_uri"]),
            "description": str(goal.get("description", "")),
        }
        for rule in self._rules:
            if not isinstance(rule, dict):
                continue
            if not _rule_matches(rule, status=status, goal=goal):
                continue
            recommendation = rule.get("recommendation")
            if not isinstance(recommendation, dict):
                msg = "matched scripted rule is missing recommendation object"
                raise InvalidInputError(msg)
            rendered = _substitute(copy.deepcopy(recommendation), variables)
            if explain_mode == "none":
                rendered.pop("explanation", None)
            elif explain_mode == "summary" and "explanation" in rendered:
                explanation = rendered["explanation"]
                if isinstance(explanation, dict):
                    rendered["explanation"] = {
                        "mode": "summary",
                        "summary": explanation.get("summary", rendered.get("summary", "")),
                    }
            return dict(rendered)
        msg = "scripted brain has no matching rule for current goal state"
        raise InvalidInputError(msg)
