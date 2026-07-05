"""Pexpect-based PTY action execution (§9.8 extension)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.secrets import resolve_secret_ref
from wayfinder.exec.shell_exec import CommandResult, _build_env, _resolve_cwd, redact_text


def _require_pexpect() -> Any:
    try:
        import pexpect
    except ImportError as exc:
        msg = "pexpect is required for wayfinder-exec-pty; install with the machines extra"
        raise InvalidInputError(msg) from exc
    return pexpect


class _TranscriptCollector:
    def __init__(self, parts: list[str]) -> None:
        self._parts = parts

    def write(self, data: str) -> None:
        self._parts.append(data)

    def flush(self) -> None:
        return None


@dataclass
class _DialogueOutcome:
    exit_code: int | None
    timed_out: bool
    redaction_values: list[str]


def _dialogue_steps(shell: dict[str, Any]) -> list[dict[str, Any]]:
    dialogue = shell.get("x_expect_dialogue")
    if not isinstance(dialogue, list) or not dialogue:
        msg = "pty actions require shell.x_expect_dialogue"
        raise InvalidInputError(msg)
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(dialogue):
        if not isinstance(step, dict):
            msg = f"x_expect_dialogue[{index}] must be an object"
            raise InvalidInputError(msg)
        steps.append(step)
    return steps


def _shell_from_action(action: dict[str, Any]) -> dict[str, Any]:
    shell = action.get("shell")
    if not isinstance(shell, dict):
        msg = "shell action missing shell object"
        raise ValueError(msg)
    if shell.get("pty") is not True:
        msg = "execute_pty_action requires shell.pty true"
        raise ValueError(msg)
    argv = shell.get("argv")
    if not isinstance(argv, list) or not argv:
        msg = "shell.argv must be a non-empty array"
        raise ValueError(msg)
    return shell


def _run_dialogue_step(
    child: Any,
    step: dict[str, Any],
    *,
    pexpect: Any,
    secrets_path: Path | None,
    redaction_values: list[str],
) -> str | None:
    expect_pattern = step.get("expect")
    if isinstance(expect_pattern, str) and expect_pattern:
        child.expect(expect_pattern)
    if "send_secret_ref" in step:
        secret_value = resolve_secret_ref(str(step["send_secret_ref"]), secrets_path=secrets_path)
        redaction_values.append(secret_value)
        child.sendline(secret_value)
        return None
    if "send" in step:
        child.sendline(str(step["send"]))
        return None
    then = step.get("then")
    if then in {"eof", "exit"}:
        child.expect(pexpect.EOF)
        return "done"
    return None


def _run_dialogue(
    child: Any,
    shell: dict[str, Any],
    *,
    pexpect: Any,
    secrets_path: Path | None,
    timeout_seconds: int,
    transcript_parts: list[str],
) -> _DialogueOutcome:
    redaction_values: list[str] = []
    exit_code: int | None = None
    timed_out = False
    try:
        for step in _dialogue_steps(shell):
            outcome = _run_dialogue_step(
                child,
                step,
                pexpect=pexpect,
                secrets_path=secrets_path,
                redaction_values=redaction_values,
            )
            if outcome == "done":
                exit_code = child.exitstatus
                break
        else:
            if child.isalive():
                child.expect(pexpect.EOF, timeout=timeout_seconds)
            exit_code = child.exitstatus
    except pexpect.TIMEOUT:
        timed_out = True
        transcript_parts.append("\n[TIMEOUT]\n")
    except pexpect.ExceptionPexpect as exc:
        transcript_parts.append(f"\n[EXPECT ERROR: {exc}]\n")
        exit_code = 1
    finally:
        if child.isalive():
            child.close(force=True)
        if exit_code is None and not timed_out:
            exit_code = child.exitstatus
    return _DialogueOutcome(
        exit_code=exit_code,
        timed_out=timed_out,
        redaction_values=redaction_values,
    )


def _redact_transcript(transcript: str, secrets: list[str]) -> str:
    redacted = transcript
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")
    return redact_text(redacted)


def execute_pty_action(
    action: dict[str, Any],
    *,
    workspace_uri: str,
    secrets_path: Path | None = None,
) -> CommandResult:
    """Drive a shell action through a declared pexpect dialogue table."""
    pexpect = _require_pexpect()
    shell = _shell_from_action(action)
    command = [str(part) for part in shell["argv"]]
    cwd = _resolve_cwd(shell, workspace_uri)
    env = _build_env(shell)
    timeout_seconds = int(shell.get("timeout_seconds", 300))
    started_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    transcript_parts: list[str] = []

    child = pexpect.spawn(
        command[0],
        command[1:],
        cwd=str(cwd),
        env={**os.environ, **env},
        encoding="utf-8",
        timeout=timeout_seconds,
    )
    child.logfile_read = _TranscriptCollector(transcript_parts)
    outcome = _run_dialogue(
        child,
        shell,
        pexpect=pexpect,
        secrets_path=secrets_path,
        timeout_seconds=timeout_seconds,
        transcript_parts=transcript_parts,
    )
    ended_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    transcript = _redact_transcript("".join(transcript_parts), outcome.redaction_values)
    return CommandResult(
        exit_code=outcome.exit_code,
        signal=None,
        timed_out=outcome.timed_out,
        stdout=transcript.encode("utf-8"),
        stderr=b"",
        started_at=started_at,
        ended_at=ended_at,
    )


def build_pty_action_result(
    command_result: CommandResult,
    *,
    action: dict[str, Any],
    artifact_store: Any,
    inline_limit: int,
) -> dict[str, Any]:
    """Map a PTY command result to wip.action_result with transcript artifact."""
    from wayfinder.core.artifacts import ArtifactStore
    from wayfinder.exec.shell_exec import build_action_result

    result = build_action_result(
        command_result,
        action=action,
        artifact_store=artifact_store,
        inline_limit=0,
    )
    output = result.setdefault("output", {})
    transcript = command_result.stdout.decode("utf-8", errors="replace")
    if artifact_store is not None and transcript and isinstance(artifact_store, ArtifactStore):
        ref = artifact_store.write_bytes(
            transcript.encode("utf-8"),
            artifact_id="art_pty_transcript",
            media_type="text/plain",
            description="pty session transcript (redacted)",
        )
        artifact_store.verify_reference(ref)
        result.setdefault("artifacts", []).append(ref)
        output["pty_transcript_artifact"] = ref["artifact_id"]
        output.pop("stdout", None)
        output.pop("stdout_artifact", None)
    elif transcript:
        if len(transcript.encode("utf-8")) <= inline_limit:
            output["pty_transcript"] = transcript
        else:
            output["pty_transcript"] = transcript[:inline_limit]
    return result
