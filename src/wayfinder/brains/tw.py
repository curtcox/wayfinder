"""Taskwarrior-backed brain: urgency math picks the next step (§9.1)."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.errors import InvalidInputError

_IDEM_PREFIX = "idem_tw_"
_ARGV_ANNOTATION = re.compile(r"^argv:(.+)$", re.IGNORECASE)
_DEPENDS_UNTIL = re.compile(
    r"can't run until|cannot run until|until the (.+?) exists|depends on",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskSeed:
    """Initial task definition before import into Taskwarrior."""

    description: str
    argv: list[str] | None = None
    depends_on: tuple[int, ...] = ()
    due: str | None = None
    priority: str | None = None
    risk_classes: tuple[str, ...] = ()


def _workspace_from_goal(goal: dict[str, Any]) -> Path:
    uri = goal.get("workspace_uri")
    if not isinstance(uri, str):
        msg = "goal missing workspace_uri"
        raise InvalidInputError(msg)
    return parse_workspace_uri(uri)


def _task_data_dir(goal: dict[str, Any]) -> Path:
    workspace = _workspace_from_goal(goal)
    goal_id = str(goal["goal_id"])
    return workspace / ".wayfinder-tw" / goal_id


def _seeds_from_metadata(goal: dict[str, Any]) -> list[TaskSeed] | None:
    metadata = goal.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw_tasks = metadata.get("tw_tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return None
    seeds: list[TaskSeed] = []
    for item in raw_tasks:
        if not isinstance(item, dict):
            msg = "tw_tasks entries must be objects"
            raise InvalidInputError(msg)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            msg = "tw_tasks entry missing description"
            raise InvalidInputError(msg)
        argv = item.get("argv")
        parsed_argv: list[str] | None = None
        if argv is not None:
            if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
                msg = "tw_tasks argv must be a string array"
                raise InvalidInputError(msg)
            parsed_argv = list(argv)
        depends_raw = item.get("depends_on", [])
        depends: tuple[int, ...] = ()
        if depends_raw:
            if not isinstance(depends_raw, list) or not all(
                isinstance(index, int) for index in depends_raw
            ):
                msg = "tw_tasks depends_on must be an integer array"
                raise InvalidInputError(msg)
            depends = tuple(depends_raw)
        due = item.get("due")
        priority = item.get("priority")
        risk_raw = item.get("risk_classes", [])
        risk_classes: tuple[str, ...] = ()
        if risk_raw:
            if not isinstance(risk_raw, list) or not all(
                isinstance(value, str) for value in risk_raw
            ):
                msg = "tw_tasks risk_classes must be a string array"
                raise InvalidInputError(msg)
            risk_classes = tuple(risk_raw)
        seeds.append(
            TaskSeed(
                description=description.strip(),
                argv=parsed_argv,
                depends_on=depends,
                due=str(due) if due is not None else None,
                priority=str(priority) if priority is not None else None,
                risk_classes=risk_classes,
            ),
        )
    return seeds


def _seeds_from_description(description: str) -> list[TaskSeed]:
    """Best-effort clause split for goals without explicit tw_tasks metadata."""
    clauses = [part.strip() for part in re.split(r"[.\n]+", description) if part.strip()]
    if not clauses:
        msg = "goal description is empty; cannot seed Taskwarrior tasks"
        raise InvalidInputError(msg)
    seeds: list[TaskSeed] = []
    for index, clause in enumerate(clauses):
        if _DEPENDS_UNTIL.search(clause):
            continue
        depends: tuple[int, ...] = ()
        if index > 0 and _DEPENDS_UNTIL.search(description):
            depends = (index - 1,)
        seeds.append(TaskSeed(description=clause, depends_on=depends))
    if not seeds:
        seeds.append(TaskSeed(description=description.strip()))
    return seeds


def _parse_argv_annotation(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list) or not all(isinstance(part, str) for part in parsed):
            msg = f"argv annotation must decode to a string array: {value!r}"
            raise InvalidInputError(msg)
        return list(parsed)
    return shlex.split(stripped)


def _argv_from_annotations(annotations: list[str]) -> list[str] | None:
    for annotation in annotations:
        match = _ARGV_ANNOTATION.match(annotation.strip())
        if match is None:
            continue
        return _parse_argv_annotation(match.group(1))
    return None


def _risk_from_annotations(annotations: list[str]) -> tuple[str, ...]:
    classes: list[str] = []
    for annotation in annotations:
        lowered = annotation.lower()
        if lowered.startswith("risk:"):
            classes.extend(part.strip() for part in annotation.split(":", 1)[1].split(","))
    return tuple(classes)


def _trim_explanation(
    recommendation: dict[str, Any],
    explain_mode: str,
) -> dict[str, Any]:
    if explain_mode == "none":
        recommendation.pop("explanation", None)
    elif explain_mode == "summary" and "explanation" in recommendation:
        explanation = recommendation["explanation"]
        if isinstance(explanation, dict):
            recommendation["explanation"] = {
                "mode": "summary",
                "summary": explanation.get("summary", recommendation.get("summary", "")),
            }
    return recommendation


class TaskBackend(Protocol):
    """Minimal Taskwarrior surface used by the brain."""

    def ensure_seeded(self, goal: dict[str, Any], seeds: list[TaskSeed]) -> None: ...

    def sync_events(self, events: list[dict[str, Any]]) -> None: ...

    def next_ready(self) -> dict[str, Any] | None: ...

    def pending_count(self) -> int: ...

    def urgency_summary(self, task_uuid: str) -> str: ...


class SubprocessTaskBackend:
    """Taskwarrior backend backed by the real `task` binary."""

    def __init__(self, *, task_bin: str, data_dir: Path) -> None:
        self._task_bin = task_bin
        self._data_dir = data_dir
        self._taskrc = data_dir / "taskrc"
        self._recommendation_keys: dict[str, str] = {}

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        env = {
            "TASKDATA": str(self._data_dir / "data"),
            "TASKRC": str(self._taskrc),
        }
        return subprocess.run(  # nosec B603
            [self._task_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _ensure_layout(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / "data").mkdir(parents=True, exist_ok=True)
        if not self._taskrc.is_file():
            self._taskrc.write_text(
                f"data.location={self._data_dir / 'data'}\n",
                encoding="utf-8",
            )

    def _export(self, *, extra_filter: str | None = None) -> list[dict[str, Any]]:
        args = ["export"]
        if extra_filter:
            args.append(extra_filter)
        proc = self._run(args)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            msg = f"task export failed: {detail}"
            raise InvalidInputError(msg)
        if not proc.stdout.strip():
            return []
        parsed = json.loads(proc.stdout)
        if not isinstance(parsed, list):
            msg = "task export returned non-array JSON"
            raise InvalidInputError(msg)
        return [item for item in parsed if isinstance(item, dict)]

    def ensure_seeded(self, goal: dict[str, Any], seeds: list[TaskSeed]) -> None:
        del goal
        self._ensure_layout()
        if self._export():
            return
        created: list[str] = []
        for seed in seeds:
            add_args = ["add", seed.description]
            if seed.priority:
                add_args.append(f"priority:{seed.priority}")
            if seed.due:
                add_args.append(f"due:{seed.due}")
            proc = self._run(add_args)
            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
                msg = f"task add failed: {detail}"
                raise InvalidInputError(msg)
            task_uuid = proc.stdout.strip().split()[-1]
            created.append(task_uuid)
            if seed.argv:
                argv_text = " ".join(shlex.quote(part) for part in seed.argv)
                annotate = self._run(["annotate", task_uuid, f"argv:{argv_text}"])
                if annotate.returncode != 0:
                    detail = (
                        annotate.stderr.strip()
                        or annotate.stdout.strip()
                        or f"exit {annotate.returncode}"
                    )
                    msg = f"task annotate failed: {detail}"
                    raise InvalidInputError(msg)
            for risk_class in seed.risk_classes:
                risk = self._run(["annotate", task_uuid, f"risk:{risk_class}"])
                if risk.returncode != 0:
                    detail = risk.stderr.strip() or risk.stdout.strip() or f"exit {risk.returncode}"
                    msg = f"task annotate failed: {detail}"
                    raise InvalidInputError(msg)
        for index, seed in enumerate(seeds):
            for dep_index in seed.depends_on:
                if dep_index < 0 or dep_index >= len(created):
                    msg = f"tw_tasks depends_on index out of range: {dep_index}"
                    raise InvalidInputError(msg)
                dep = self._run(["dep", "add", created[index], created[dep_index]])
                if dep.returncode != 0:
                    detail = dep.stderr.strip() or dep.stdout.strip() or f"exit {dep.returncode}"
                    msg = f"task dep add failed: {detail}"
                    raise InvalidInputError(msg)

    def sync_events(self, events: list[dict[str, Any]]) -> None:
        completed: set[str] = set()
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
            if not isinstance(idempotency, dict):
                continue
            key = idempotency.get("key")
            if isinstance(recommendation_id, str) and isinstance(key, str):
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
            if key is None or not key.startswith(_IDEM_PREFIX):
                continue
            task_uuid = key.removeprefix(_IDEM_PREFIX)
            if task_uuid in completed:
                continue
            done = self._run(["done", task_uuid])
            if done.returncode != 0:
                detail = done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}"
                msg = f"task done failed: {detail}"
                raise InvalidInputError(msg)
            completed.add(task_uuid)

    def next_ready(self) -> dict[str, Any] | None:
        ready = self._export(extra_filter="+READY")
        if not ready:
            return None
        ready.sort(key=lambda item: float(item.get("urgency", 0)), reverse=True)
        return ready[0]

    def pending_count(self) -> int:
        pending = self._export(extra_filter="status:pending")
        return len(pending)

    def urgency_summary(self, task_uuid: str) -> str:
        proc = self._run(["info", task_uuid])
        if proc.returncode != 0:
            return f"urgency unavailable for {task_uuid}"
        for line in proc.stdout.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("urgency"):
                return stripped
        return proc.stdout.strip() or f"urgency unavailable for {task_uuid}"


class TwBrain:
    """Issue the highest-urgency ready Taskwarrior task for a goal."""

    def __init__(self, backend: TaskBackend | None = None) -> None:
        self._backend_override = backend
        self._task_bin: str | None = None

    def _task_bin_path(self) -> str:
        if self._task_bin is None:
            found = shutil.which("task")
            if found is None:
                msg = "task (Taskwarrior) is not installed or not on PATH"
                raise InvalidInputError(msg)
            self._task_bin = found
        return self._task_bin

    def _backend_for_goal(self, goal: dict[str, Any]) -> TaskBackend:
        if self._backend_override is not None:
            return self._backend_override
        data_dir = _task_data_dir(goal)
        return SubprocessTaskBackend(task_bin=self._task_bin_path(), data_dir=data_dir)

    def _seeds_for_goal(self, goal: dict[str, Any]) -> list[TaskSeed]:
        from_metadata = _seeds_from_metadata(goal)
        if from_metadata is not None:
            return from_metadata
        description = goal.get("description")
        if not isinstance(description, str):
            msg = "goal missing description for task seeding"
            raise InvalidInputError(msg)
        return _seeds_from_description(description)

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        del status
        backend = self._backend_for_goal(goal)
        backend.ensure_seeded(goal, self._seeds_for_goal(goal))
        backend.sync_events(events)
        if backend.pending_count() == 0:
            return _trim_explanation(
                {
                    "recommendation_type": "done",
                    "summary": "All Taskwarrior tasks are complete.",
                    "goal_status": "running",
                    "confidence": 0.95,
                    "done": {"reason": "No pending tasks remain."},
                    "explanation": {
                        "mode": "structured",
                        "summary": "Taskwarrior reports no pending tasks for this goal.",
                        "evidence": [],
                        "redactions": [],
                    },
                },
                explain_mode,
            )
        task = backend.next_ready()
        if task is None:
            return _trim_explanation(
                {
                    "recommendation_type": "blocked",
                    "summary": "Tasks remain but none are ready yet.",
                    "goal_status": "running",
                    "confidence": 0.9,
                    "blocked": {
                        "reason": "Waiting on Taskwarrior dependencies.",
                    },
                    "explanation": {
                        "mode": "structured",
                        "summary": (
                            "Pending tasks exist but dependencies block every ready queue entry."
                        ),
                        "evidence": [],
                        "redactions": [],
                    },
                },
                explain_mode,
            )
        task_uuid = str(task.get("uuid", ""))
        description = str(task.get("description", "next task"))
        annotations = task.get("annotations")
        annotation_list = annotations if isinstance(annotations, list) else []
        argv = _argv_from_annotations([str(item) for item in annotation_list])
        urgency = float(task.get("urgency", 0))
        urgency_line = backend.urgency_summary(task_uuid)
        if argv is None:
            return _trim_explanation(
                _question_recommendation(
                    task_uuid=task_uuid,
                    description=description,
                    urgency=urgency,
                    urgency_line=urgency_line,
                    explain_mode=explain_mode,
                ),
                explain_mode,
            )
        workspace_uri = str(goal["workspace_uri"])
        risk_classes = _risk_from_annotations([str(item) for item in annotation_list])
        return _trim_explanation(
            _action_recommendation(
                task_uuid=task_uuid,
                description=description,
                argv=argv,
                workspace_uri=workspace_uri,
                urgency=urgency,
                urgency_line=urgency_line,
                mode=mode,
                risk_classes=risk_classes,
                explain_mode=explain_mode,
            ),
            explain_mode,
        )


def _question_recommendation(
    *,
    task_uuid: str,
    description: str,
    urgency: float,
    urgency_line: str,
    explain_mode: str,
) -> dict[str, Any]:
    del explain_mode
    return {
        "recommendation_type": "question",
        "summary": f"Handle task: {description}",
        "goal_status": "running",
        "confidence": 0.85,
        "question": {
            "prompt": f"Taskwarrior selected {description!r} but it has no argv annotation.",
            "response_schema": {"type": "object", "additionalProperties": True},
        },
        "idempotency": {
            "level": "weak",
            "key": f"{_IDEM_PREFIX}{task_uuid}",
            "scope": "goal",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": False,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "manual",
            "max_attempts": 1,
        },
        "explanation": {
            "mode": "structured",
            "summary": f"{description} (urgency {urgency:.1f}). {urgency_line}.",
            "evidence": [],
            "redactions": [],
        },
    }


def _action_recommendation(
    *,
    task_uuid: str,
    description: str,
    argv: list[str],
    workspace_uri: str,
    urgency: float,
    urgency_line: str,
    mode: str,
    risk_classes: tuple[str, ...],
    explain_mode: str,
) -> dict[str, Any]:
    del explain_mode
    display = " ".join(argv)
    requires_approval = "network_write" in risk_classes
    risk_level = "medium" if requires_approval else "low"
    classes = list(risk_classes) if risk_classes else ["read_local", "execute_local"]
    preview_note = " (preview only)" if mode == "preview" else ""
    return {
        "recommendation_type": "action",
        "summary": f"{description}: {display}",
        "goal_status": "running",
        "confidence": 0.9,
        "executable": mode != "preview",
        "action": {
            "kind": "shell",
            "title": description,
            "shell": {
                "argv": argv,
                "command_for_display": display,
                "cwd": workspace_uri,
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 600,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [
                {"id": "succ_exit", "kind": "exit_code", "operator": "in", "value": [0]},
            ],
        },
        "idempotency": {
            "level": "strong",
            "key": f"{_IDEM_PREFIX}{task_uuid}",
            "scope": "goal",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 2,
        },
        "risk": {
            "level": risk_level,
            "classes": classes,
            "blast_radius": "workspace",
            "requires_approval": requires_approval,
            "destructive": False,
            "network": "required" if "network_write" in classes else "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": (
                f"Taskwarrior ready queue{preview_note}: {description} "
                f"(urgency {urgency:.1f}). {urgency_line}."
            ),
            "evidence": [],
            "redactions": [],
        },
    }
