"""Behavior-tree brain: standing goals via py_trees (§9.5)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.errors import InvalidInputError

_IDEM_PREFIX = "idem_bt_"


def _require_py_trees() -> Any:
    try:
        import py_trees
    except ImportError as exc:
        msg = "py_trees is required for wayfinder-bt; install with the machines extra"
        raise InvalidInputError(msg) from exc
    return py_trees


def _workspace_from_goal(goal: dict[str, Any]) -> Path:
    uri = goal.get("workspace_uri")
    if not isinstance(uri, str):
        msg = "goal missing workspace_uri"
        raise InvalidInputError(msg)
    return parse_workspace_uri(uri)


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


@dataclass(frozen=True)
class ActionSpec:
    """Shell action leaf."""

    name: str
    title: str
    argv: tuple[str, ...]
    timeout_seconds: int = 600
    expected_exit_codes: tuple[int, ...] = (0,)
    risk_classes: tuple[str, ...] = ("read_local", "execute_local")


@dataclass(frozen=True)
class WaitSpec:
    """Wait-until-time leaf."""

    name: str
    interval_seconds: int
    summary: str


@dataclass(frozen=True)
class QuestionSpec:
    """Human escalation leaf."""

    name: str
    prompt: str
    summary: str


@dataclass(frozen=True)
class ConditionSpec:
    """Predicate over recent action outcomes."""

    name: str
    check: str


@dataclass
class TreeNode:
    """Parsed behavior-tree node."""

    type: str
    name: str | None = None
    children: list[TreeNode] = field(default_factory=list)
    child: TreeNode | None = None
    action: ActionSpec | None = None
    wait: WaitSpec | None = None
    question: QuestionSpec | None = None
    condition: ConditionSpec | None = None


@dataclass
class ActionOutcome:
    """One completed shell action extracted from the event log."""

    node_name: str
    exit_code: int
    completed_at: str
    idempotency_key: str


@dataclass
class WaitOutcome:
    """One issued wait recommendation extracted from the event log."""

    node_name: str
    until_time: str
    idempotency_key: str


@dataclass
class BlackboardState:
    """Event-log-derived state for tree evaluation."""

    actions: list[ActionOutcome] = field(default_factory=list)
    waits: list[WaitOutcome] = field(default_factory=list)
    reference_time: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def action_completions(self, node_name: str) -> list[ActionOutcome]:
        return [item for item in self.actions if item.node_name == node_name]

    def wait_completions(self, node_name: str) -> list[WaitOutcome]:
        return [item for item in self.waits if item.node_name == node_name]

    @classmethod
    def from_events(
        cls,
        events: list[dict[str, Any]],
        *,
        reference_time: datetime | None = None,
    ) -> BlackboardState:
        issued_waits: dict[str, WaitOutcome] = {}
        actions: list[ActionOutcome] = []
        for event in events:
            event_type = str(event.get("type", ""))
            if event_type == "recommendation.issued":
                data = event.get("data", {})
                if not isinstance(data, dict):
                    continue
                recommendation = data.get("recommendation", {})
                if not isinstance(recommendation, dict):
                    continue
                if recommendation.get("recommendation_type") != "wait":
                    continue
                wait_payload = recommendation.get("wait", {})
                if not isinstance(wait_payload, dict):
                    continue
                until_time = wait_payload.get("until_time")
                if not isinstance(until_time, str):
                    continue
                idempotency = recommendation.get("idempotency", {})
                key = ""
                if isinstance(idempotency, dict):
                    key = str(idempotency.get("key", ""))
                node_name = _node_name_from_idempotency(key)
                if node_name:
                    issued_waits[key] = WaitOutcome(
                        node_name=node_name,
                        until_time=until_time,
                        idempotency_key=key,
                    )
                continue
            if event_type not in {"action.completed", "action.failed"}:
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            result = data.get("action_result", {})
            if not isinstance(result, dict):
                continue
            idempotency_key = str(result.get("idempotency_key", ""))
            node_name = _node_name_from_idempotency(idempotency_key)
            if not node_name:
                continue
            exit_code = 0 if event_type == "action.completed" else 1
            shell = result.get("shell", {})
            if isinstance(shell, dict) and "exit_code" in shell:
                exit_code = int(shell["exit_code"])
            actions.append(
                ActionOutcome(
                    node_name=node_name,
                    exit_code=exit_code,
                    completed_at=str(event.get("time", "")),
                    idempotency_key=idempotency_key,
                ),
            )
        waits = list(issued_waits.values())
        ref = reference_time or datetime.now(tz=UTC)
        return cls(actions=actions, waits=waits, reference_time=ref)


def _node_name_from_idempotency(key: str) -> str:
    if not key.startswith(_IDEM_PREFIX):
        return ""
    remainder = key[len(_IDEM_PREFIX) :]
    if "_" not in remainder:
        return remainder
    return remainder.rsplit("_", 1)[0]


def _idempotency_key(node_name: str, attempt: int) -> str:
    return f"{_IDEM_PREFIX}{node_name}_{attempt}"


def _parse_tree_node(raw: dict[str, Any]) -> TreeNode:
    node_type = raw.get("type")
    if not isinstance(node_type, str) or not node_type.strip():
        msg = "tree node missing type"
        raise InvalidInputError(msg)
    name = raw.get("name")
    parsed_name = str(name) if name is not None else None
    if node_type == "action":
        argv_raw = raw.get("argv")
        if not isinstance(argv_raw, list) or not all(isinstance(part, str) for part in argv_raw):
            msg = f"action node {parsed_name!r} requires argv string array"
            raise InvalidInputError(msg)
        if not parsed_name:
            msg = "action nodes require name"
            raise InvalidInputError(msg)
        title = str(raw.get("title", parsed_name))
        timeout = int(raw.get("timeout_seconds", 600))
        codes_raw = raw.get("expected_exit_codes", [0])
        if not isinstance(codes_raw, list):
            msg = f"expected_exit_codes must be an array for {parsed_name}"
            raise InvalidInputError(msg)
        codes = tuple(int(code) for code in codes_raw)
        risk_raw = raw.get("risk_classes", ["read_local", "execute_local"])
        risk: tuple[str, ...] = ()
        if isinstance(risk_raw, list):
            risk = tuple(str(item) for item in risk_raw)
        return TreeNode(
            type=node_type,
            name=parsed_name,
            action=ActionSpec(
                name=parsed_name,
                title=title,
                argv=tuple(argv_raw),
                timeout_seconds=timeout,
                expected_exit_codes=codes,
                risk_classes=risk or ("read_local", "execute_local"),
            ),
        )
    if node_type == "wait":
        if not parsed_name:
            msg = "wait nodes require name"
            raise InvalidInputError(msg)
        interval = int(raw.get("interval_seconds", 60))
        summary = str(raw.get("summary", f"Wait {interval}s before next check"))
        return TreeNode(
            type=node_type,
            name=parsed_name,
            wait=WaitSpec(name=parsed_name, interval_seconds=interval, summary=summary),
        )
    if node_type == "question":
        if not parsed_name:
            msg = "question nodes require name"
            raise InvalidInputError(msg)
        prompt = str(raw.get("prompt", "Human input required."))
        summary = str(raw.get("summary", prompt))
        return TreeNode(
            type=node_type,
            name=parsed_name,
            question=QuestionSpec(name=parsed_name, prompt=prompt, summary=summary),
        )
    if node_type == "condition":
        if not parsed_name:
            msg = "condition nodes require name"
            raise InvalidInputError(msg)
        check = str(raw.get("check", "last_action_success"))
        return TreeNode(
            type=node_type,
            name=parsed_name,
            condition=ConditionSpec(name=parsed_name, check=check),
        )
    if node_type == "repeat":
        child_raw = raw.get("child")
        if not isinstance(child_raw, dict):
            msg = "repeat node requires child object"
            raise InvalidInputError(msg)
        return TreeNode(
            type=node_type,
            name=parsed_name or "repeat",
            child=_parse_tree_node(child_raw),
        )
    if node_type in {"sequence", "selector"}:
        children_raw = raw.get("children")
        if not isinstance(children_raw, list) or not children_raw:
            msg = f"{node_type} node requires non-empty children array"
            raise InvalidInputError(msg)
        return TreeNode(
            type=node_type,
            name=parsed_name or node_type,
            children=[_parse_tree_node(item) for item in children_raw if isinstance(item, dict)],
        )
    msg = f"unsupported tree node type: {node_type}"
    raise InvalidInputError(msg)


def load_tree(path: Path) -> TreeNode:
    """Load a .bt JSON behavior tree."""
    if not path.is_file():
        msg = f"tree file not found: {path}"
        raise InvalidInputError(msg)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        msg = "tree file must contain a JSON object"
        raise InvalidInputError(msg)
    root_raw = parsed.get("root", parsed)
    if not isinstance(root_raw, dict):
        msg = "tree root must be an object"
        raise InvalidInputError(msg)
    return _parse_tree_node(root_raw)


def _parse_rfc3339(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _condition_satisfied(spec: ConditionSpec, state: BlackboardState) -> bool:
    if not state.actions:
        return spec.check == "no_actions_yet"
    last = state.actions[-1]
    if spec.check == "last_action_success":
        return last.exit_code == 0
    if spec.check == "last_action_failed":
        return last.exit_code != 0
    if spec.check.startswith("action_success:"):
        node_name = spec.check.split(":", 1)[1]
        completions = state.action_completions(node_name)
        return bool(completions) and completions[-1].exit_code == 0
    if spec.check.startswith("action_failed:"):
        node_name = spec.check.split(":", 1)[1]
        completions = state.action_completions(node_name)
        return bool(completions) and completions[-1].exit_code != 0
    msg = f"unsupported condition check: {spec.check}"
    raise InvalidInputError(msg)


def _action_attempt(state: BlackboardState, node_name: str) -> int:
    return len(state.action_completions(node_name))


def _wait_attempt(state: BlackboardState, node_name: str) -> int:
    return len(state.wait_completions(node_name))


def _wait_elapsed(state: BlackboardState, spec: WaitSpec) -> bool:
    waits = state.wait_completions(spec.name)
    if not waits:
        return False
    until_time = _parse_rfc3339(waits[-1].until_time)
    return state.reference_time >= until_time


class _PendingRecommendation:
    """Marker set by leaf behaviours when a recommendation is needed."""

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None


def _condition_behaviour(
    spec: ConditionSpec,
    *,
    state: BlackboardState,
    py_trees: Any,
) -> Any:
    class ConditionBehaviour(py_trees.behaviour.Behaviour):
        def update(self) -> Any:
            if _condition_satisfied(spec, state):
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE

    return ConditionBehaviour(name=spec.name)


def _action_behaviour(
    spec: ActionSpec,
    *,
    state: BlackboardState,
    workspace_uri: str,
    mode: str,
    explain_mode: str,
    pending: _PendingRecommendation,
    py_trees: Any,
) -> Any:
    class ActionBehaviour(py_trees.behaviour.Behaviour):
        def update(self) -> Any:
            completions = state.action_completions(spec.name)
            if completions:
                return py_trees.common.Status.SUCCESS
            attempt = _action_attempt(state, spec.name)
            pending.payload = _action_recommendation(
                spec=spec,
                workspace_uri=workspace_uri,
                attempt=attempt,
                mode=mode,
                explain_mode=explain_mode,
            )
            return py_trees.common.Status.RUNNING

    return ActionBehaviour(name=spec.name)


def _wait_behaviour(
    spec: WaitSpec,
    *,
    state: BlackboardState,
    explain_mode: str,
    pending: _PendingRecommendation,
    py_trees: Any,
) -> Any:
    class WaitBehaviour(py_trees.behaviour.Behaviour):
        def update(self) -> Any:
            if _wait_elapsed(state, spec):
                return py_trees.common.Status.SUCCESS
            attempt = _wait_attempt(state, spec.name)
            until = state.reference_time + timedelta(seconds=spec.interval_seconds)
            pending.payload = _wait_recommendation(
                spec=spec,
                until_time=until,
                attempt=attempt,
                explain_mode=explain_mode,
            )
            return py_trees.common.Status.RUNNING

    return WaitBehaviour(name=spec.name)


def _question_behaviour(
    spec: QuestionSpec,
    *,
    state: BlackboardState,
    explain_mode: str,
    pending: _PendingRecommendation,
    py_trees: Any,
) -> Any:
    class QuestionBehaviour(py_trees.behaviour.Behaviour):
        def update(self) -> Any:
            pending.payload = _question_recommendation(
                spec=spec,
                attempt=_action_attempt(state, spec.name),
                explain_mode=explain_mode,
            )
            return py_trees.common.Status.RUNNING

    return QuestionBehaviour(name=spec.name)


def _build_py_tree(
    node: TreeNode,
    *,
    state: BlackboardState,
    goal: dict[str, Any],
    mode: str,
    explain_mode: str,
    pending: _PendingRecommendation,
) -> Any:
    py_trees = _require_py_trees()
    workspace_uri = str(goal["workspace_uri"])

    if node.type == "sequence":
        composite = py_trees.composites.Sequence(name=node.name or "sequence", memory=True)
        composite.add_children(
            [
                _build_py_tree(
                    child,
                    state=state,
                    goal=goal,
                    mode=mode,
                    explain_mode=explain_mode,
                    pending=pending,
                )
                for child in node.children
            ],
        )
        return composite
    if node.type == "selector":
        composite = py_trees.composites.Selector(name=node.name or "selector", memory=True)
        composite.add_children(
            [
                _build_py_tree(
                    child,
                    state=state,
                    goal=goal,
                    mode=mode,
                    explain_mode=explain_mode,
                    pending=pending,
                )
                for child in node.children
            ],
        )
        return composite
    if node.type == "repeat":
        if node.child is None:
            msg = "repeat node missing child"
            raise InvalidInputError(msg)
        child_tree = _build_py_tree(
            node.child,
            state=state,
            goal=goal,
            mode=mode,
            explain_mode=explain_mode,
            pending=pending,
        )
        return py_trees.decorators.Repeat(
            name=node.name or "repeat",
            child=child_tree,
            num_success=-1,
        )

    if node.type == "condition":
        condition_spec = node.condition
        if condition_spec is None:
            msg = "condition node missing spec"
            raise InvalidInputError(msg)
        return _condition_behaviour(condition_spec, state=state, py_trees=py_trees)

    if node.type == "action":
        action_spec = node.action
        if action_spec is None:
            msg = "action node missing spec"
            raise InvalidInputError(msg)
        return _action_behaviour(
            action_spec,
            state=state,
            workspace_uri=workspace_uri,
            mode=mode,
            explain_mode=explain_mode,
            pending=pending,
            py_trees=py_trees,
        )

    if node.type == "wait":
        wait_spec = node.wait
        if wait_spec is None:
            msg = "wait node missing spec"
            raise InvalidInputError(msg)
        return _wait_behaviour(
            wait_spec,
            state=state,
            explain_mode=explain_mode,
            pending=pending,
            py_trees=py_trees,
        )

    if node.type == "question":
        question_spec = node.question
        if question_spec is None:
            msg = "question node missing spec"
            raise InvalidInputError(msg)
        return _question_behaviour(
            question_spec,
            state=state,
            explain_mode=explain_mode,
            pending=pending,
            py_trees=py_trees,
        )

    msg = f"unsupported tree node type: {node.type}"
    raise InvalidInputError(msg)


def _action_recommendation(
    *,
    spec: ActionSpec,
    workspace_uri: str,
    attempt: int,
    mode: str,
    explain_mode: str,
) -> dict[str, Any]:
    display = " ".join(spec.argv)
    requires_approval = "network_write" in spec.risk_classes
    risk_level = "medium" if requires_approval else "low"
    preview_note = " (preview only)" if mode == "preview" else ""
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": f"{spec.title}: {display}",
        "goal_status": "running",
        "confidence": 0.9,
        "executable": mode != "preview",
        "action": {
            "kind": "shell",
            "title": spec.title,
            "shell": {
                "argv": list(spec.argv),
                "command_for_display": display,
                "cwd": workspace_uri,
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": spec.timeout_seconds,
                "expected_exit_codes": list(spec.expected_exit_codes),
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [
                {
                    "id": "succ_exit",
                    "kind": "exit_code",
                    "operator": "in",
                    "value": list(spec.expected_exit_codes),
                },
            ],
        },
        "idempotency": {
            "level": "strong",
            "key": _idempotency_key(spec.name, attempt),
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
            "classes": list(spec.risk_classes),
            "blast_radius": "workspace",
            "requires_approval": requires_approval,
            "destructive": False,
            "network": "required" if requires_approval else "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": f"Behavior tree action{preview_note}: {spec.title}.",
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _wait_recommendation(
    *,
    spec: WaitSpec,
    until_time: datetime,
    attempt: int,
    explain_mode: str,
) -> dict[str, Any]:
    until = until_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    recommendation: dict[str, Any] = {
        "recommendation_type": "wait",
        "summary": spec.summary,
        "goal_status": "running",
        "confidence": 0.95,
        "wait": {"until_time": until},
        "idempotency": {
            "level": "weak",
            "key": _idempotency_key(spec.name, attempt),
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
            "summary": f"{spec.summary} (until {until}).",
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _question_recommendation(
    *,
    spec: QuestionSpec,
    attempt: int,
    explain_mode: str,
) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "question",
        "summary": spec.summary,
        "goal_status": "waiting",
        "confidence": 0.85,
        "question": {
            "question_id": f"q_{spec.name}",
            "prompt": spec.prompt,
            "response_schema": {"type": "object", "additionalProperties": True},
        },
        "idempotency": {
            "level": "weak",
            "key": _idempotency_key(spec.name, attempt),
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
            "summary": spec.summary,
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


class BtBrain:
    """Tick a py_trees behavior tree against event-log state."""

    def __init__(self, tree_path: Path) -> None:
        self._tree_path = tree_path.resolve()
        self._root = load_tree(self._tree_path)

    @property
    def tree_path(self) -> Path:
        return self._tree_path

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
        _workspace_from_goal(goal)
        reference_raw = goal.get("metadata", {})
        reference_time: datetime | None = None
        if isinstance(reference_raw, dict):
            ref_value = reference_raw.get("reference_time")
            if isinstance(ref_value, str):
                reference_time = _parse_rfc3339(ref_value)
        state = BlackboardState.from_events(events, reference_time=reference_time)
        pending = _PendingRecommendation()
        py_tree = _build_py_tree(
            self._root,
            state=state,
            goal=goal,
            mode=mode,
            explain_mode=explain_mode,
            pending=pending,
        )
        py_trees = _require_py_trees()
        py_tree.setup_with_descendants()
        for _ in range(64):
            py_tree.tick_once()
            if pending.payload is not None:
                return pending.payload
            if py_tree.status == py_trees.common.Status.RUNNING:
                continue
            pending.payload = None
            py_tree.stop(py_trees.common.Status.INVALID)
            py_tree.setup_with_descendants()
        msg = "behavior tree exceeded tick limit without producing a recommendation"
        raise InvalidInputError(msg)
