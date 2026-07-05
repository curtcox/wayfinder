"""Make-backed brain: walk out-of-date targets and issue build recipes (§9.3)."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.errors import InvalidInputError

_MAKE_META = re.compile(r"^make(\[\d+\])?:|^Makefile:")


def _workspace_from_goal(goal: dict[str, Any]) -> Path:
    uri = goal.get("workspace_uri")
    if not isinstance(uri, str):
        msg = "goal missing workspace_uri"
        raise InvalidInputError(msg)
    return parse_workspace_uri(uri)


def _ensure_make() -> str:
    make = shutil.which("make")
    if make is None:
        msg = "make is not installed or not on PATH"
        raise InvalidInputError(msg)
    return make


def _parse_dry_run(output: str) -> list[list[str]]:
    """Return argv lists for each shell recipe line make would run."""
    commands: list[list[str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _MAKE_META.match(stripped):
            continue
        try:
            commands.append(shlex.split(stripped))
        except ValueError as exc:
            msg = f"unable to parse make recipe line: {stripped!r}"
            raise InvalidInputError(msg) from exc
    return commands


def _run_make(
    make: str,
    workspace: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [make, *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )


def _target_up_to_date(make: str, workspace: Path, target: str) -> bool:
    proc = _run_make(make, workspace, ["-q", target])
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    msg = f"make -q {target!r} failed: {detail}"
    raise InvalidInputError(msg)


def _remaining_commands(make: str, workspace: Path, target: str) -> list[list[str]]:
    proc = _run_make(make, workspace, ["-n", target])
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        msg = f"make -n {target!r} failed: {detail}"
        raise InvalidInputError(msg)
    return _parse_dry_run(proc.stdout)


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


def _done_recommendation(*, target: str, explain_mode: str) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "recommendation_type": "done",
        "summary": f"Target {target} is up to date.",
        "goal_status": "running",
        "confidence": 0.95,
        "done": {"reason": f"{target} is up to date."},
        "explanation": {
            "mode": "structured",
            "summary": f"make reports nothing to rebuild for {target}.",
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


_PREVIEW_REMAINING_LIMIT = 3


def _action_recommendation(
    *,
    target: str,
    argv: list[str],
    workspace_uri: str,
    remaining: list[list[str]],
    mode: str,
    explain_mode: str,
) -> dict[str, Any]:
    display = " ".join(argv)
    remaining_summary = "; ".join(
        " ".join(command) for command in remaining[1:_PREVIEW_REMAINING_LIMIT]
    )
    preview_tail = len(remaining) > _PREVIEW_REMAINING_LIMIT
    if preview_tail:
        remaining_summary = f"{remaining_summary}; …" if remaining_summary else "…"
    preview_note = " (preview only)" if mode == "preview" else ""
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": f"Build step toward {target}: {display}",
        "goal_status": "running",
        "confidence": 0.95,
        "executable": mode != "preview",
        "action": {
            "kind": "shell",
            "title": display,
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
            "key": f"idem_make_{target}_{display}",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": True,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 2,
        },
        "risk": {
            "level": "low",
            "classes": ["read_local", "execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
        "explanation": {
            "mode": "structured",
            "summary": (
                f"Next out-of-date recipe for {target}{preview_note}: {display}."
                + (f" Remaining: {remaining_summary}." if remaining_summary else "")
            ),
            "evidence": [],
            "redactions": [],
        },
    }
    return _trim_explanation(recommendation, explain_mode)


class MakeBrain:
    """Issue make recipes until a build target is up to date."""

    def __init__(self, target: str) -> None:
        if not target.strip():
            msg = "make brain requires a non-empty target"
            raise InvalidInputError(msg)
        self._target = target.strip()
        self._make = _ensure_make()

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        del status, events
        workspace = _workspace_from_goal(goal)
        if not (workspace / "Makefile").is_file() and not (workspace / "makefile").is_file():
            msg = f"no Makefile found in workspace: {workspace}"
            raise InvalidInputError(msg)
        workspace_uri = str(goal["workspace_uri"])
        if _target_up_to_date(self._make, workspace, self._target):
            return _done_recommendation(target=self._target, explain_mode=explain_mode)
        remaining = _remaining_commands(self._make, workspace, self._target)
        if not remaining:
            return _done_recommendation(target=self._target, explain_mode=explain_mode)
        return _action_recommendation(
            target=self._target,
            argv=remaining[0],
            workspace_uri=workspace_uri,
            remaining=remaining,
            mode=mode,
            explain_mode=explain_mode,
        )
