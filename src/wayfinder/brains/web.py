"""Browser-action brain: structured web steps as WIP shell actions (§9.10)."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any

from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.errors import LLMError

_IDEM_PREFIX = "idem_web_"
_AGENT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_RUNNER_ARGV = (sys.executable, "-m", "wayfinder.web.runner")
_SIDE_EFFECT_OPS = frozenset({"click", "submit", "select"})
_READ_ONLY_OPS = frozenset({"navigate", "await_download", "screenshot"})


@dataclass(frozen=True)
class WebScript:
    """Scripted browser session step for deterministic web runs."""

    title: str
    steps: tuple[dict[str, Any], ...]
    risk_classes: tuple[str, ...] = ("network_read",)


@dataclass(frozen=True)
class BrowserTurn:
    """One proposed browser script and its reported result."""

    step: int
    title: str
    steps: list[dict[str, Any]]
    status: str
    summary: str


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


def _parse_web_steps(goal: dict[str, Any]) -> list[WebScript] | None:
    metadata = goal.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("web_steps")
    if not isinstance(raw, list) or not raw:
        return None
    scripts: list[WebScript] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            msg = f"web_steps[{index}] must be an object"
            raise InvalidInputError(msg)
        steps_raw = item.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            msg = f"web_steps[{index}] requires non-empty steps array"
            raise InvalidInputError(msg)
        steps: list[dict[str, Any]] = []
        for step_index, step in enumerate(steps_raw):
            if not isinstance(step, dict) or not isinstance(step.get("op"), str):
                msg = f"web_steps[{index}].steps[{step_index}] must include op"
                raise InvalidInputError(msg)
            steps.append(dict(step))
        title = str(item.get("title", f"Browser step {index + 1}"))
        risk_raw = item.get("risk_classes", ["network_read"])
        risk: tuple[str, ...] = ()
        if isinstance(risk_raw, list):
            risk = tuple(str(class_name) for class_name in risk_raw)
        scripts.append(
            WebScript(
                title=title,
                steps=tuple(steps),
                risk_classes=risk or ("network_read",),
            ),
        )
    return scripts


def _step_from_idempotency_key(key: str) -> int | None:
    if not key.startswith(_IDEM_PREFIX):
        return None
    suffix = key[len(_IDEM_PREFIX) :]
    try:
        return int(suffix)
    except ValueError:
        return None


def _result_summary(result: dict[str, Any]) -> str:
    output = result.get("output", {})
    if isinstance(output, dict):
        transcript = output.get("browser_transcript") or output.get("stdout", "")
        if transcript:
            return str(transcript)[:500]
    return str(result.get("status", "unknown"))


def _browser_history_from_events(events: list[dict[str, Any]]) -> list[BrowserTurn]:
    pending: dict[int, BrowserTurn] = {}
    history: list[BrowserTurn] = []
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
            browser_steps = shell.get("x_browser_steps")
            if not isinstance(browser_steps, list):
                continue
            idempotency = recommendation.get("idempotency", {})
            key = ""
            if isinstance(idempotency, dict):
                key = str(idempotency.get("key", ""))
            step = _step_from_idempotency_key(key)
            if step is None:
                continue
            pending[step] = BrowserTurn(
                step=step,
                title=str(action.get("title", "browser action")),
                steps=[dict(item) for item in browser_steps if isinstance(item, dict)],
                status="issued",
                summary="",
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
        status = str(result.get("status", "unknown"))
        summary = _result_summary(result)
        turn = pending.get(step)
        if turn is None:
            turn = BrowserTurn(
                step=step,
                title=f"step {step}",
                steps=[],
                status=status,
                summary=summary,
            )
        else:
            turn = BrowserTurn(
                step=turn.step,
                title=turn.title,
                steps=turn.steps,
                status=status,
                summary=summary,
            )
        pending[step] = turn
        while step in pending:
            history.append(pending.pop(step))
            step += 1
    return history


def _completed_step_count(history: list[BrowserTurn]) -> int:
    return sum(1 for turn in history if turn.status in {"completed", "failed"})


def _infer_risk_classes(steps: list[dict[str, Any]]) -> tuple[str, ...]:
    ops = {str(step.get("op", "")) for step in steps}
    if ops & _SIDE_EFFECT_OPS:
        return ("network_write", "external_side_effect")
    if ops <= _READ_ONLY_OPS:
        return ("network_read",)
    return ("network_read", "external_side_effect")


def _web_system_prompt() -> str:
    return (
        "You are wayfinder-web, a browser automation brain for WIP v0.1. You achieve goals "
        "by proposing one browser script at a time. You never drive the browser yourself — "
        "each proposal becomes an auditable shell action whose x_browser_steps field carries "
        "the script. Return exactly one JSON object with these fields:\n"
        '- decision: "run_browser" | "done" | "blocked" | "question"\n'
        "- steps: array of step objects with op in "
        "navigate|fill|click|await_download|screenshot\n"
        "- title: short human title for the action\n"
        "- summary: one-line recommendation summary\n"
        "- reasoning: agent reasoning surfaced via explain\n"
        "- question: prompt string (required when decision is question)\n"
        "- blocked_reason: string (required when decision is blocked)\n"
        "Use secret_ref on fill steps for credentials. Prefer inspect-first navigation."
    )


def _parse_agent_json(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate)
    match = _AGENT_JSON_RE.search(candidate)
    if match is None:
        msg = "web agent response did not contain JSON"
        raise LLMError(msg)
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        msg = f"web agent response was not valid JSON: {exc}"
        raise LLMError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "web agent response must be a JSON object"
        raise LLMError(msg)
    return parsed


def _agent_messages(
    *,
    goal: dict[str, Any],
    history: list[BrowserTurn],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _web_system_prompt()},
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
                        "proposed_steps": turn.steps,
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
                            "summary": turn.summary,
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
            "question_id": f"q_web_{step}",
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
    steps: list[dict[str, Any]],
    title: str,
    summary: str,
    workspace_uri: str,
    step: int,
    reasoning: str,
    risk_classes: tuple[str, ...],
    mode: str,
    explain_mode: str,
) -> dict[str, Any]:
    requires_approval = any(
        class_name in {"network_write", "external_side_effect"} for class_name in risk_classes
    )
    network = "required"
    preview_note = " (preview only)" if mode == "preview" else ""
    display = " ".join(_RUNNER_ARGV)
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
                "argv": list(_RUNNER_ARGV),
                "command_for_display": display,
                "cwd": workspace_uri,
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 900,
                "expected_exit_codes": [0],
                "requires_shell": False,
                "x_browser_steps": steps,
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
            "blast_radius": "external",
            "requires_approval": requires_approval,
            "destructive": False,
            "network": network,
            "secrets": "required" if any("secret_ref" in step for step in steps) else "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": f"{reasoning or summary}{preview_note}",
            "evidence": [
                {"kind": "agent_reasoning", "text": reasoning or summary},
                {"kind": "browser_steps", "step": step, "steps": steps},
            ],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


class WebBrain:
    """Browser automation brain that issues one web script per next call."""

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
        workspace_uri = _workspace_uri(goal)
        history = _browser_history_from_events(events)
        completed = _completed_step_count(history)
        progress = status.get("progress", {})
        if isinstance(progress, dict) and isinstance(progress.get("completed_steps"), int):
            completed = max(completed, progress["completed_steps"])
        scripted = _parse_web_steps(goal)
        if scripted is not None:
            if completed >= len(scripted):
                return _done_recommendation(
                    summary="Scripted web steps complete.",
                    reasoning="All scripted browser steps finished.",
                    explain_mode=explain_mode,
                )
            script = scripted[completed]
            return _action_recommendation(
                steps=[dict(step) for step in script.steps],
                title=script.title,
                summary=f"Web step {completed + 1}/{len(scripted)}: {script.title}",
                workspace_uri=workspace_uri,
                step=completed,
                reasoning=f"Scripted browser step {completed + 1}: {script.title}",
                risk_classes=script.risk_classes,
                mode=mode,
                explain_mode=explain_mode,
            )

        if self._llm_client is None:
            msg = (
                "configure an LLM endpoint or provide goal.metadata.web_steps "
                "for deterministic web runs"
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
                if decision == "run_browser":
                    steps_raw = agent.get("steps")
                    if not isinstance(steps_raw, list) or not steps_raw:
                        last_error = "run_browser decision requires non-empty steps array"
                    else:
                        steps = [dict(item) for item in steps_raw if isinstance(item, dict)]
                        if not steps or not all(isinstance(step.get("op"), str) for step in steps):
                            last_error = "each browser step must include op"
                        else:
                            title = str(agent.get("title", "Browser action"))
                            summary = str(agent.get("summary", title))
                            risk_classes = _infer_risk_classes(steps)
                            return _action_recommendation(
                                steps=steps,
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
        msg = f"web agent failed after retries: {last_error}"
        raise LLMError(msg)
