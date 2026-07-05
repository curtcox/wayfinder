"""Taskwarrior brain unit tests."""

from __future__ import annotations

from typing import Any

import pytest

from wayfinder.brains.tw import (
    TaskSeed,
    TwBrain,
    _argv_from_annotations,
    _parse_argv_annotation,
    _seeds_from_description,
    _seeds_from_metadata,
)


class FakeTaskBackend:
    """In-memory stand-in for Taskwarrior used in unit tests."""

    def __init__(self) -> None:
        self.seeds: list[TaskSeed] = []
        self.tasks: list[dict[str, Any]] = []
        self.completed: set[str] = set()
        self._recommendation_keys: dict[str, str] = {}

    def ensure_seeded(self, goal: dict[str, Any], seeds: list[TaskSeed]) -> None:
        del goal
        if self.tasks:
            return
        self.seeds = list(seeds)
        for index, seed in enumerate(seeds):
            annotations: list[str] = []
            if seed.argv:
                annotations.append(f"argv:{' '.join(seed.argv)}")
            annotations.extend(f"risk:{risk_class}" for risk_class in seed.risk_classes)
            self.tasks.append(
                {
                    "uuid": f"uuid_{index + 1}",
                    "description": seed.description,
                    "urgency": 10.0 - index,
                    "annotations": annotations,
                    "depends_on": list(seed.depends_on),
                    "status": "pending",
                },
            )

    def sync_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            if event.get("type") != "recommendation.issued":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            recommendation = data.get("recommendation", {})
            if not isinstance(recommendation, dict):
                continue
            recommendation_id = recommendation.get("recommendation_id")
            idempotency = recommendation.get("idempotency", {})
            if isinstance(recommendation_id, str) and isinstance(idempotency, dict):
                key = idempotency.get("key")
                if isinstance(key, str):
                    self._recommendation_keys[recommendation_id] = key
        for event in events:
            if event.get("type") != "action.completed":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            recommendation_id = data.get("recommendation_id")
            if not isinstance(recommendation_id, str):
                continue
            key = self._recommendation_keys.get(recommendation_id)
            if key is None or not key.startswith("idem_tw_"):
                continue
            self.completed.add(key.removeprefix("idem_tw_"))

    def _ready_tasks(self) -> list[dict[str, Any]]:
        done = self.completed
        ready: list[dict[str, Any]] = []
        for task in self.tasks:
            if task["uuid"] in done:
                continue
            blocked = False
            for dep_index in task.get("depends_on", []):
                dep_uuid = self.tasks[dep_index]["uuid"]
                if dep_uuid not in done:
                    blocked = True
                    break
            if not blocked:
                ready.append(task)
        ready.sort(key=lambda item: float(item["urgency"]), reverse=True)
        return ready

    def next_ready(self) -> dict[str, Any] | None:
        ready = self._ready_tasks()
        return ready[0] if ready else None

    def pending_count(self) -> int:
        return len([task for task in self.tasks if task["uuid"] not in self.completed])

    def urgency_summary(self, task_uuid: str) -> str:
        for task in self.tasks:
            if task["uuid"] == task_uuid:
                return f"Urgency {task['urgency']:.1f}"
        return f"urgency unavailable for {task_uuid}"


def test_parse_argv_annotation_supports_shell_and_json() -> None:
    assert _parse_argv_annotation("echo hello") == ["echo", "hello"]
    assert _parse_argv_annotation('["echo", "hello"]') == ["echo", "hello"]


def test_argv_from_annotations() -> None:
    assert _argv_from_annotations(["note", "argv:make test"]) == ["make", "test"]
    assert _argv_from_annotations(["note only"]) is None


def test_seeds_from_metadata() -> None:
    goal = {
        "metadata": {
            "tw_tasks": [
                {"description": "export logs", "argv": ["export.sh"]},
                {"description": "run audit", "depends_on": [0], "argv": ["audit.sh"]},
            ],
        },
    }
    seeds = _seeds_from_metadata(goal)
    assert seeds is not None
    assert len(seeds) == 2
    assert seeds[1].depends_on == (0,)


def test_seeds_from_description_skips_dependency_sentence() -> None:
    description = (
        "Export the access logs. Run the audit script. "
        "The audit script can't run until the export exists."
    )
    seeds = _seeds_from_description(description)
    assert [seed.description for seed in seeds] == [
        "Export the access logs",
        "Run the audit script",
    ]


def test_tw_brain_issues_first_ready_action() -> None:
    backend = FakeTaskBackend()
    brain = TwBrain(backend=backend)
    goal = {
        "goal_id": "goal_tw_01",
        "workspace_uri": "file:/tmp/workspace",
        "description": "ignored",
        "metadata": {
            "tw_tasks": [
                {"description": "export access logs", "argv": ["export-logs.sh"]},
                {"description": "run audit script", "depends_on": [0], "argv": ["audit.sh"]},
            ],
        },
    }
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="summary",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["argv"] == ["export-logs.sh"]
    assert recommendation["idempotency"]["key"] == "idem_tw_uuid_1"


def test_tw_brain_blocks_until_dependency_completes() -> None:
    backend = FakeTaskBackend()
    brain = TwBrain(backend=backend)
    goal = {
        "goal_id": "goal_tw_02",
        "workspace_uri": "file:/tmp/workspace",
        "metadata": {
            "tw_tasks": [
                {"description": "export access logs", "argv": ["export-logs.sh"]},
                {"description": "run audit script", "depends_on": [0], "argv": ["audit.sh"]},
            ],
        },
    }
    seeds = [
        TaskSeed(description="export access logs", argv=["export-logs.sh"]),
        TaskSeed(description="run audit script", argv=["audit.sh"], depends_on=(0,)),
    ]
    backend.ensure_seeded(goal, seeds)
    backend.completed.add("uuid_1")
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "running"},
        events=[],
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["argv"] == ["audit.sh"]


def test_tw_brain_done_when_all_tasks_complete() -> None:
    backend = FakeTaskBackend()
    brain = TwBrain(backend=backend)
    goal = {
        "goal_id": "goal_tw_03",
        "workspace_uri": "file:/tmp/workspace",
        "metadata": {"tw_tasks": [{"description": "one step", "argv": ["true"]}]},
    }
    backend.ensure_seeded(
        goal,
        [TaskSeed(description="one step", argv=["true"])],
    )
    backend.completed.add("uuid_1")
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "running", "completed_steps": 1},
        events=[],
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "done"


def test_tw_brain_question_without_argv() -> None:
    backend = FakeTaskBackend()
    brain = TwBrain(backend=backend)
    goal = {
        "goal_id": "goal_tw_04",
        "workspace_uri": "file:/tmp/workspace",
        "metadata": {"tw_tasks": [{"description": "call the vendor"}]},
    }
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "question"


def test_tw_brain_network_write_requires_approval() -> None:
    backend = FakeTaskBackend()
    brain = TwBrain(backend=backend)
    goal = {
        "goal_id": "goal_tw_05",
        "workspace_uri": "file:/tmp/workspace",
        "metadata": {
            "tw_tasks": [
                {
                    "description": "file the report",
                    "argv": ["upload-report.sh"],
                    "risk_classes": ["network_write"],
                },
            ],
        },
    }
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="none",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert risk["requires_approval"] is True
    assert "network_write" in risk["classes"]


def test_seeds_from_metadata_rejects_bad_entry() -> None:
    with pytest.raises(Exception, match="description"):
        _seeds_from_metadata({"metadata": {"tw_tasks": [{"argv": ["true"]}]}})
