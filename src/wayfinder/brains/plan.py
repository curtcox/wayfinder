"""PDDL planner brain: classical plans dealt one step per next (§9.2)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError

_IDEM_PREFIX = "idem_plan_"
_OPERATOR_RE = re.compile(r"^\((.+)\)$")


class _OperatorLike(Protocol):
    name: str
    preconditions: frozenset[str]
    add_effects: frozenset[str]
    del_effects: frozenset[str]


@dataclass(frozen=True)
class ActionBinding:
    """Maps a grounded planner operator to a shell argv."""

    argv: tuple[str, ...]
    title: str
    risk_classes: tuple[str, ...] = ("read_local", "execute_local")


@dataclass
class PlanProgress:
    """How far execution advanced in the current plan generation."""

    generation: int = 0
    completed_steps: int = 0
    replan_requested: bool = False
    init_override: list[str] | None = None


def _require_pyperplan() -> Any:
    try:
        from pyperplan import planner as pyperplan_planner
    except ImportError as exc:
        msg = "pyperplan is required for wayfinder-plan; install with the machines extra"
        raise InvalidInputError(msg) from exc
    return pyperplan_planner


def _workspace_from_goal(goal: dict[str, Any]) -> Path:
    uri = goal.get("workspace_uri")
    if not isinstance(uri, str):
        msg = "goal missing workspace_uri"
        raise InvalidInputError(msg)
    return parse_workspace_uri(uri)


def _operator_label(operator: _OperatorLike) -> str:
    match = _OPERATOR_RE.match(operator.name.strip())
    if match is None:
        return operator.name.strip()
    return match.group(1)


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


def _parse_action_bindings(goal: dict[str, Any]) -> dict[str, ActionBinding]:
    metadata = goal.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("plan_actions")
    if not isinstance(raw, dict):
        return {}
    bindings: dict[str, ActionBinding] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        argv_raw = value.get("argv")
        if not isinstance(argv_raw, list) or not all(isinstance(part, str) for part in argv_raw):
            msg = f"plan_actions[{key!r}] requires argv string array"
            raise InvalidInputError(msg)
        title = str(value.get("title", key))
        risk_raw = value.get("risk_classes", ["read_local", "execute_local"])
        risk: tuple[str, ...] = ()
        if isinstance(risk_raw, list):
            risk = tuple(str(item) for item in risk_raw)
        bindings[key] = ActionBinding(
            argv=tuple(argv_raw),
            title=title,
            risk_classes=risk or ("read_local", "execute_local"),
        )
    return bindings


def _problem_from_metadata(goal: dict[str, Any]) -> str | None:
    metadata = goal.get("metadata")
    if not isinstance(metadata, dict):
        return None
    problem = metadata.get("pddl_problem")
    if isinstance(problem, str) and problem.strip():
        return problem.strip()
    return None


def _problem_system_prompt(domain_text: str) -> str:
    return (
        "You compile natural-language goals into a PDDL problem file for the supplied domain. "
        "Return only the problem PDDL text (a single define form). "
        "Use predicates and action names from the domain. "
        "Encode safety invariants as goal conjuncts or init facts rather than prose. "
        "Do not wrap the response in markdown fences."
        f"\n\nDomain:\n{domain_text}"
    )


def _validate_problem_pddl(domain_path: Path, problem_text: str) -> None:
    pyperplan_planner = _require_pyperplan()
    with tempfile.TemporaryDirectory() as tmp:
        problem_path = Path(tmp) / "problem.pddl"
        problem_path.write_text(problem_text, encoding="utf-8")
        try:
            pyperplan_planner._parse(str(domain_path), str(problem_path))
        except Exception as exc:
            msg = f"compiled PDDL problem failed to parse: {exc}"
            raise InvalidInputError(msg) from exc


def _compile_problem_with_llm(
    *,
    domain_path: Path,
    goal: dict[str, Any],
    events: list[dict[str, Any]],
    init_override: list[str] | None,
    client: ChatClient,
) -> str:
    domain_text = domain_path.read_text(encoding="utf-8")
    user_payload = {
        "goal_description": goal.get("description", ""),
        "recent_events": events[-20:],
        "init_override": init_override,
    }
    conversation = [
        {"role": "system", "content": _problem_system_prompt(domain_text)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    last_error = "unknown compilation error"
    content = ""
    for _attempt in range(3):
        content = client.complete(conversation, json_mode=False)
        candidate = content.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-z]*\n?", "", candidate)
            candidate = re.sub(r"\n?```$", "", candidate)
        try:
            _validate_problem_pddl(domain_path, candidate)
        except InvalidInputError as exc:
            last_error = str(exc)
            conversation = [
                *conversation,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous PDDL failed validation: "
                        f"{last_error}. Return corrected PDDL only."
                    ),
                },
            ]
        else:
            return candidate
    msg = f"LLM failed to compile a valid PDDL problem: {last_error}"
    raise LLMError(msg)


def _write_problem(workspace: Path, goal_id: str, problem_text: str) -> Path:
    plan_dir = workspace / ".wayfinder-plan" / goal_id
    plan_dir.mkdir(parents=True, exist_ok=True)
    problem_path = plan_dir / "problem.pddl"
    problem_path.write_text(problem_text, encoding="utf-8")
    return problem_path


def _parse_init_facts(problem_text: str) -> list[str]:
    match = re.search(r"\(:init\b(.*?)\(:goal", problem_text, flags=re.DOTALL | re.IGNORECASE)
    if match is None:
        return []
    init_block = match.group(1)
    return [match.strip() for match in re.findall(r"\([^()]+\)", init_block)]


def _replace_init(problem_text: str, facts: list[str]) -> str:
    if not facts:
        return problem_text
    init_body = "\n  ".join(facts)
    replaced, count = re.subn(
        r"\(:init\b.*?\)\s*(?=\(:goal)",
        f"(:init\n  {init_body}\n)\n",
        problem_text,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if count == 0:
        msg = "unable to replace (:init ...) in PDDL problem"
        raise InvalidInputError(msg)
    return replaced


def _progress_from_events(events: list[dict[str, Any]]) -> PlanProgress:
    progress = PlanProgress()
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type == "observation.recorded":
            data = event.get("data", {})
            if isinstance(data, dict):
                observation = data.get("observation", {})
                if isinstance(observation, dict):
                    metadata = observation.get("metadata", {})
                    if isinstance(metadata, dict):
                        if metadata.get("pddl_replan") is True:
                            progress.replan_requested = True
                        init_override = metadata.get("pddl_init")
                        if isinstance(init_override, list) and all(
                            isinstance(item, str) for item in init_override
                        ):
                            progress.init_override = list(init_override)
                            progress.replan_requested = True
            continue
        if event_type not in {"action.completed", "action.failed"}:
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        result = data.get("action_result", {})
        if not isinstance(result, dict):
            continue
        key = str(result.get("idempotency_key", ""))
        if not key.startswith(_IDEM_PREFIX):
            continue
        suffix = key[len(_IDEM_PREFIX) :]
        if "_" not in suffix:
            continue
        generation_raw, step_raw = suffix.split("_", 1)
        try:
            generation = int(generation_raw)
            step = int(step_raw)
        except ValueError:
            continue
        if generation > progress.generation:
            progress.generation = generation
            progress.completed_steps = 0
            progress.replan_requested = False
        if generation != progress.generation:
            continue
        if event_type == "action.failed":
            progress.replan_requested = True
            progress.completed_steps = min(progress.completed_steps, step)
            continue
        if step == progress.completed_steps:
            progress.completed_steps += 1
    return progress


def _solve_with_pyperplan(domain_path: Path, problem_path: Path) -> list[Any]:
    pyperplan_planner = _require_pyperplan()
    parsed = pyperplan_planner._parse(str(domain_path), str(problem_path))
    task = pyperplan_planner._ground(parsed)
    plan = pyperplan_planner.SEARCHES["gbf"](task, pyperplan_planner.HEURISTICS["hadd"](task))
    if plan is None:
        return []
    return list(plan)


def _solve_with_fast_downward(domain_path: Path, problem_path: Path) -> list[str] | None:
    downward = shutil.which("fast-downward.py") or shutil.which("downward")
    if downward is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        sas_path = Path(tmp) / "output.sas"
        plan_path = Path(tmp) / "plan"
        translate = subprocess.run(  # nosec B603
            [downward, "--translate", str(domain_path), str(problem_path)],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        if translate.returncode != 0:
            return None
        search = subprocess.run(  # nosec B603
            [downward, str(sas_path), "--search", "astar(lmcut())"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        if search.returncode != 0:
            return None
        if not plan_path.is_file():
            return None
        return [
            line.strip()
            for line in plan_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(";")
        ]


def _plan_steps(domain_path: Path, problem_path: Path) -> list[_OperatorLike]:
    downward_plan = _solve_with_fast_downward(domain_path, problem_path)
    if downward_plan is not None:
        pyperplan_planner = _require_pyperplan()
        parsed = pyperplan_planner._parse(str(domain_path), str(problem_path))
        task = pyperplan_planner._ground(parsed)
        by_name = {_operator_label(op): op for op in task.operators}
        resolved: list[_OperatorLike] = []
        for line in downward_plan:
            stripped = line.strip().strip("()")
            if stripped in by_name:
                resolved.append(by_name[stripped])
        if resolved:
            return resolved
    return _solve_with_pyperplan(domain_path, problem_path)


def _plan_evidence(
    plan: list[_OperatorLike],
    *,
    current_index: int,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, operator in enumerate(plan):
        status = "pending"
        if index < current_index:
            status = "completed"
        elif index == current_index:
            status = "current"
        evidence.append(
            {
                "kind": "plan_step",
                "step": index,
                "action": _operator_label(operator),
                "preconditions": sorted(operator.preconditions),
                "status": status,
            },
        )
    return evidence


def _idempotency_key(*, generation: int, step: int) -> str:
    return f"{_IDEM_PREFIX}{generation}_{step}"


def _lookup_binding(
    operator: _OperatorLike,
    bindings: dict[str, ActionBinding],
) -> ActionBinding | None:
    label = _operator_label(operator)
    if label in bindings:
        return bindings[label]
    action_name = label.split(" ", 1)[0]
    if action_name in bindings:
        return bindings[action_name]
    return None


def _blocked_recommendation(*, summary: str, explain_mode: str) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "blocked",
        "summary": summary,
        "goal_status": "blocked",
        "confidence": 0.9,
        "blocked": {"reason": summary},
        "explanation": {
            "mode": "structured",
            "summary": summary,
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _question_recommendation(
    *,
    prompt: str,
    summary: str,
    generation: int,
    step: int,
    explain_mode: str,
) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "question",
        "summary": summary,
        "goal_status": "waiting",
        "confidence": 0.8,
        "question": {
            "question_id": f"q_plan_{generation}_{step}",
            "prompt": prompt,
            "response_schema": {"type": "object", "additionalProperties": True},
        },
        "idempotency": {
            "level": "weak",
            "key": _idempotency_key(generation=generation, step=step),
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
            "summary": summary,
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _done_recommendation(*, summary: str, explain_mode: str) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "done",
        "summary": summary,
        "goal_status": "running",
        "confidence": 0.95,
        "done": {"reason": summary},
        "explanation": {
            "mode": "structured",
            "summary": summary,
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _action_recommendation(
    *,
    operator: _OperatorLike,
    binding: ActionBinding,
    workspace_uri: str,
    generation: int,
    step: int,
    plan: list[_OperatorLike],
    mode: str,
    explain_mode: str,
) -> dict[str, Any]:
    display = " ".join(binding.argv)
    label = _operator_label(operator)
    requires_approval = "network_write" in binding.risk_classes
    risk_level = "medium" if requires_approval else "low"
    preview_note = " (preview only)" if mode == "preview" else ""
    remaining = [_operator_label(item) for item in plan[step + 1 : step + 4]]
    remaining_summary = ", ".join(remaining)
    if len(plan) > step + 4:
        remaining_summary = f"{remaining_summary}, …" if remaining_summary else "…"
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": f"Plan step {step + 1}/{len(plan)}: {binding.title}",
        "goal_status": "running",
        "confidence": 0.95,
        "executable": mode != "preview",
        "action": {
            "kind": "shell",
            "title": binding.title,
            "shell": {
                "argv": list(binding.argv),
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
            "key": _idempotency_key(generation=generation, step=step),
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
            "classes": list(binding.risk_classes),
            "blast_radius": "workspace",
            "requires_approval": requires_approval,
            "destructive": False,
            "network": "required" if requires_approval else "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": (
                f"Planner step {step + 1}/{len(plan)}{preview_note}: {label} → {display}."
                + (f" Remaining: {remaining_summary}." if remaining_summary else "")
            ),
            "evidence": _plan_evidence(plan, current_index=step),
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


class PlanBrain:
    """Compile PDDL problems, plan with pyperplan, and issue one step per next."""

    def __init__(self, domain_path: Path, *, llm_client: ChatClient | None = None) -> None:
        resolved = domain_path.resolve()
        if not resolved.is_file():
            msg = f"PDDL domain file not found: {resolved}"
            raise InvalidInputError(msg)
        self._domain_path = resolved
        self._llm_client = llm_client

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
        workspace = _workspace_from_goal(goal)
        goal_id = str(goal["goal_id"])
        bindings = _parse_action_bindings(goal)
        progress = _progress_from_events(events)
        generation = progress.generation
        if progress.replan_requested:
            generation += 1

        problem_text = _problem_from_metadata(goal)
        if problem_text is None:
            if self._llm_client is None:
                msg = (
                    "goal metadata must include pddl_problem or configure an LLM endpoint "
                    "to compile the problem from prose"
                )
                raise InvalidInputError(msg)
            problem_text = _compile_problem_with_llm(
                domain_path=self._domain_path,
                goal=goal,
                events=events,
                init_override=progress.init_override,
                client=self._llm_client,
            )
        elif progress.init_override is not None:
            problem_text = _replace_init(problem_text, progress.init_override)
        elif progress.replan_requested and progress.completed_steps > 0:
            base_facts = _parse_init_facts(problem_text)
            state = set(base_facts)
            prior_plan = _plan_steps(
                self._domain_path,
                _write_problem(workspace, goal_id, problem_text),
            )
            for operator in prior_plan[: progress.completed_steps]:
                state -= operator.del_effects
                state |= operator.add_effects
            problem_text = _replace_init(problem_text, sorted(state))

        problem_path = _write_problem(workspace, goal_id, problem_text)
        plan = _plan_steps(self._domain_path, problem_path)
        if not plan:
            return _blocked_recommendation(
                summary="Planner found no plan for the current PDDL problem.",
                explain_mode=explain_mode,
            )
        step_index = 0 if progress.replan_requested else progress.completed_steps
        if step_index >= len(plan):
            return _done_recommendation(
                summary="Planner goal satisfied; plan complete.",
                explain_mode=explain_mode,
            )
        operator = plan[step_index]
        binding = _lookup_binding(operator, bindings)
        if binding is None:
            label = _operator_label(operator)
            return _question_recommendation(
                prompt=(
                    f"The planner produced action {label!r}, but no plan_actions binding "
                    "maps it to argv. Provide argv metadata or answer how to execute it."
                ),
                summary=f"No argv mapping for planner action {label}",
                generation=generation,
                step=step_index,
                explain_mode=explain_mode,
            )
        return _action_recommendation(
            operator=operator,
            binding=binding,
            workspace_uri=str(goal["workspace_uri"]),
            generation=generation,
            step=step_index,
            plan=plan,
            mode=mode,
            explain_mode=explain_mode,
        )
