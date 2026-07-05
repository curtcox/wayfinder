"""Agentic coding brain: intercept shell proposals as WIP actions (§9.9)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError

_IDEM_PREFIX = "idem_codex_"
_AGENT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class CodexStep:
    """Scripted shell step for deterministic codex runs."""

    argv: tuple[str, ...]
    title: str
    risk_classes: tuple[str, ...] = ("read_local", "execute_local")


@dataclass(frozen=True)
class ToolTurn:
    """One proposed command and its reported result."""

    step: int
    argv: list[str]
    title: str
    exit_code: int | None
    stdout: str
    stderr: str
    status: str


def _workspace_uri(goal: dict[str, Any]) -> str:
    uri = goal.get("workspace_uri")
    if not isinstance(uri, str):
        msg = "goal missing workspace_uri"
        raise InvalidInputError(msg)
    return uri


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


def _idempotency_key(step: int) -> str:
    return f"{_IDEM_PREFIX}{step}"


def _parse_codex_steps(goal: dict[str, Any]) -> list[CodexStep] | None:
    metadata = goal.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("codex_steps")
    if not isinstance(raw, list) or not raw:
        return None
    steps: list[CodexStep] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            msg = f"codex_steps[{index}] must be an object"
            raise InvalidInputError(msg)
        argv_raw = item.get("argv")
        if not isinstance(argv_raw, list) or not all(isinstance(part, str) for part in argv_raw):
            msg = f"codex_steps[{index}] requires argv string array"
            raise InvalidInputError(msg)
        title = str(item.get("title", " ".join(argv_raw)))
        risk_raw = item.get("risk_classes", ["read_local", "execute_local"])
        risk: tuple[str, ...] = ()
        if isinstance(risk_raw, list):
            risk = tuple(str(class_name) for class_name in risk_raw)
        steps.append(
            CodexStep(
                argv=tuple(argv_raw),
                title=title,
                risk_classes=risk or ("read_local", "execute_local"),
            ),
        )
    return steps


def _result_output(result: dict[str, Any]) -> tuple[str, str, int | None, str]:
    output = result.get("output", {})
    stdout = ""
    stderr = ""
    if isinstance(output, dict):
        stdout = str(output.get("stdout", ""))
        stderr = str(output.get("stderr", ""))
    shell = result.get("shell", {})
    exit_code: int | None = None
    if isinstance(shell, dict) and isinstance(shell.get("exit_code"), int):
        exit_code = shell["exit_code"]
    status = str(result.get("status", "unknown"))
    return stdout, stderr, exit_code, status


def _step_from_idempotency_key(key: str) -> int | None:
    if not key.startswith(_IDEM_PREFIX):
        return None
    suffix = key[len(_IDEM_PREFIX) :]
    try:
        return int(suffix)
    except ValueError:
        return None


def _tool_history_from_events(events: list[dict[str, Any]]) -> list[ToolTurn]:
    pending: dict[int, ToolTurn] = {}
    history: list[ToolTurn] = []
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type == "recommendation.issued":
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            recommendation = data.get("recommendation", {})
            if not isinstance(recommendation, dict):
                continue
            if recommendation.get("recommendation_type") != "action":
                continue
            action = recommendation.get("action", {})
            if not isinstance(action, dict):
                continue
            shell = action.get("shell", {})
            if not isinstance(shell, dict):
                continue
            argv = shell.get("argv")
            if not isinstance(argv, list):
                continue
            idempotency = recommendation.get("idempotency", {})
            key = ""
            if isinstance(idempotency, dict):
                key = str(idempotency.get("key", ""))
            step = _step_from_idempotency_key(key)
            if step is None:
                continue
            pending[step] = ToolTurn(
                step=step,
                argv=[str(part) for part in argv],
                title=str(action.get("title", " ".join(str(part) for part in argv))),
                exit_code=None,
                stdout="",
                stderr="",
                status="issued",
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
        key = str(result.get("idempotency_key", ""))
        step = _step_from_idempotency_key(key)
        if step is None:
            continue
        stdout, stderr, exit_code, status = _result_output(result)
        turn = pending.get(step)
        if turn is None:
            turn = ToolTurn(
                step=step,
                argv=[],
                title=f"step {step}",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                status=status,
            )
        else:
            turn = ToolTurn(
                step=turn.step,
                argv=turn.argv,
                title=turn.title,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                status=status,
            )
        pending[step] = turn
        while step in pending:
            history.append(pending.pop(step))
            step += 1
    return history


def _completed_step_count(history: list[ToolTurn]) -> int:
    return sum(1 for turn in history if turn.status in {"completed", "failed"})


def _codex_system_prompt() -> str:
    return (
        "You are wayfinder-codex, a coding agent brain for WIP v0.1. You diagnose and fix "
        "software problems by proposing shell commands one at a time. You never execute "
        "commands yourself — each proposal becomes an auditable shell action. "
        "Return exactly one JSON object with these fields:\n"
        '- decision: "run_shell" | "done" | "blocked" | "question"\n'
        "- argv: string array (required when decision is run_shell)\n"
        "- title: short human title for the action\n"
        "- summary: one-line recommendation summary\n"
        "- reasoning: agent reasoning surfaced via explain\n"
        "- question: prompt string (required when decision is question)\n"
        "- blocked_reason: string (required when decision is blocked)\n"
        "Prefer minimal, inspect-first commands. Declare done only when the goal is satisfied."
    )


def _parse_agent_json(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate)
    match = _AGENT_JSON_RE.search(candidate)
    if match is None:
        msg = "codex agent response did not contain JSON"
        raise LLMError(msg)
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        msg = f"codex agent response was not valid JSON: {exc}"
        raise LLMError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "codex agent response must be a JSON object"
        raise LLMError(msg)
    return parsed


def _agent_messages(
    *,
    goal: dict[str, Any],
    history: list[ToolTurn],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _codex_system_prompt()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal_description": goal.get("description", ""),
                    "workspace_uri": goal.get("workspace_uri"),
                    "completed_steps": _completed_step_count(history),
                },
                ensure_ascii=False,
            ),
        },
    ]
    for turn in history:
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "proposed_argv": turn.argv,
                        "title": turn.title,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "tool_result": {
                            "status": turn.status,
                            "exit_code": turn.exit_code,
                            "stdout": turn.stdout[:4000],
                            "stderr": turn.stderr[:4000],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        )
    return messages


def _done_recommendation(*, summary: str, reasoning: str, explain_mode: str) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "done",
        "summary": summary,
        "goal_status": "running",
        "confidence": 0.9,
        "done": {"reason": summary},
        "explanation": {
            "mode": "structured",
            "summary": reasoning or summary,
            "evidence": [{"kind": "agent_reasoning", "text": reasoning or summary}],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _blocked_recommendation(
    *,
    summary: str,
    reasoning: str,
    explain_mode: str,
) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "blocked",
        "summary": summary,
        "goal_status": "blocked",
        "confidence": 0.85,
        "blocked": {"reason": summary},
        "explanation": {
            "mode": "structured",
            "summary": reasoning or summary,
            "evidence": [{"kind": "agent_reasoning", "text": reasoning or summary}],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _question_recommendation(
    *,
    prompt: str,
    summary: str,
    step: int,
    reasoning: str,
    explain_mode: str,
) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "question",
        "summary": summary,
        "goal_status": "waiting",
        "confidence": 0.8,
        "question": {
            "question_id": f"q_codex_{step}",
            "prompt": prompt,
            "response_schema": {"type": "object", "additionalProperties": True},
        },
        "idempotency": {
            "level": "weak",
            "key": _idempotency_key(step),
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
            "summary": reasoning or summary,
            "evidence": [{"kind": "agent_reasoning", "text": reasoning or summary}],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _action_recommendation(
    *,
    argv: list[str],
    title: str,
    summary: str,
    workspace_uri: str,
    step: int,
    reasoning: str,
    risk_classes: tuple[str, ...],
    mode: str,
    explain_mode: str,
) -> dict[str, Any]:
    display = " ".join(argv)
    requires_approval = any(
        class_name in {"network_write", "external_side_effect"} for class_name in risk_classes
    )
    network = "required" if "network_read" in risk_classes or requires_approval else "not_required"
    preview_note = " (preview only)" if mode == "preview" else ""
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": summary,
        "goal_status": "running",
        "confidence": 0.88,
        "executable": mode != "preview",
        "action": {
            "kind": "shell",
            "title": title,
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
            "key": _idempotency_key(step),
            "scope": "goal",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": False,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 2,
        },
        "risk": {
            "level": "medium" if requires_approval else "low",
            "classes": list(risk_classes),
            "blast_radius": "workspace",
            "requires_approval": requires_approval,
            "destructive": False,
            "network": network,
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": f"{reasoning or summary}{preview_note}",
            "evidence": [
                {"kind": "agent_reasoning", "text": reasoning or summary},
                {"kind": "codex_step", "step": step, "argv": argv},
            ],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


def _infer_risk_classes(argv: list[str]) -> tuple[str, ...]:
    joined = " ".join(argv).lower()
    if any(
        marker in joined
        for marker in ("curl ", "wget ", "ssh ", "scp ", "ansible-playbook", " gh ", "npm publish")
    ):
        if any(
            marker in joined
            for marker in ("--check", " get ", " fetch ", " status", " ls ", " list ")
        ):
            return ("network_read", "execute_local")
        return ("network_write", "external_side_effect", "execute_local")
    return ("read_local", "execute_local")


class CodexBrain:
    """Coding agent that issues one shell action per next call."""

    def __init__(self, *, llm_client: ChatClient | None = None) -> None:
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
        workspace_uri = _workspace_uri(goal)
        history = _tool_history_from_events(events)
        completed = _completed_step_count(history)
        scripted = _parse_codex_steps(goal)
        if scripted is not None:
            if completed >= len(scripted):
                return _done_recommendation(
                    summary="Scripted codex steps complete.",
                    reasoning="All scripted agent steps finished.",
                    explain_mode=explain_mode,
                )
            step = scripted[completed]
            return _action_recommendation(
                argv=list(step.argv),
                title=step.title,
                summary=f"Codex step {completed + 1}/{len(scripted)}: {step.title}",
                workspace_uri=workspace_uri,
                step=completed,
                reasoning=f"Scripted codex step {completed + 1}: {step.title}",
                risk_classes=step.risk_classes,
                mode=mode,
                explain_mode=explain_mode,
            )

        if self._llm_client is None:
            msg = (
                "configure an LLM endpoint or provide goal.metadata.codex_steps "
                "for deterministic codex runs"
            )
            raise InvalidInputError(msg)

        messages = _agent_messages(goal=goal, history=history)
        last_error = "unknown agent error"
        content = ""
        for _attempt in range(3):
            content = self._llm_client.complete(messages, json_mode=True)
            try:
                agent = _parse_agent_json(content)
            except LLMError as exc:
                last_error = str(exc)
            else:
                decision = str(agent.get("decision", "")).strip()
                reasoning = str(agent.get("reasoning", agent.get("summary", "")))
                if decision == "done":
                    return _done_recommendation(
                        summary=str(agent.get("summary", "Goal satisfied.")),
                        reasoning=reasoning,
                        explain_mode=explain_mode,
                    )
                if decision == "blocked":
                    return _blocked_recommendation(
                        summary=str(agent.get("blocked_reason", agent.get("summary", "Blocked."))),
                        reasoning=reasoning,
                        explain_mode=explain_mode,
                    )
                if decision == "question":
                    prompt = str(agent.get("question", agent.get("summary", "Need input.")))
                    return _question_recommendation(
                        prompt=prompt,
                        summary=str(agent.get("summary", prompt)),
                        step=completed,
                        reasoning=reasoning,
                        explain_mode=explain_mode,
                    )
                if decision == "run_shell":
                    argv_raw = agent.get("argv")
                    if not isinstance(argv_raw, list) or not argv_raw:
                        last_error = "run_shell decision requires non-empty argv array"
                    else:
                        argv = [str(part) for part in argv_raw]
                        title = str(agent.get("title", " ".join(argv)))
                        summary = str(agent.get("summary", title))
                        risk_classes = _infer_risk_classes(argv)
                        return _action_recommendation(
                            argv=argv,
                            title=title,
                            summary=summary,
                            workspace_uri=workspace_uri,
                            step=completed,
                            reasoning=reasoning,
                            risk_classes=risk_classes,
                            mode=mode,
                            explain_mode=explain_mode,
                        )
                else:
                    last_error = f"unsupported agent decision: {decision!r}"
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous JSON was invalid or incomplete: "
                        f"{last_error}. Return corrected JSON only."
                    ),
                },
            ]
        msg = f"codex agent failed after retries: {last_error}"
        raise LLMError(msg)
