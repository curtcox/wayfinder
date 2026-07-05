"""Shell action execution helpers."""

from __future__ import annotations

import os
import re
import signal
import subprocess  # nosec B404
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import ArtifactIntegrityError

REDACTION_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+",
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
]


@dataclass(frozen=True)
class CommandResult:
    """Captured output from a spawned command."""

    exit_code: int | None
    signal: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    started_at: str
    ended_at: str


def redact_text(text: str) -> str:
    redacted = text
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _resolve_cwd(shell: dict[str, Any], workspace_uri: str) -> Path:
    workspace = parse_workspace_uri(workspace_uri)
    cwd_uri = str(shell.get("cwd", f"file:{workspace}"))
    parsed = urlparse(cwd_uri)
    if parsed.scheme != "file":
        msg = f"unsupported cwd scheme: {parsed.scheme}"
        raise ValueError(msg)
    return Path(unquote(parsed.path)).resolve()


def _build_env(shell: dict[str, Any]) -> dict[str, str]:
    env_spec = shell.get("env", {})
    if not isinstance(env_spec, dict):
        return {}
    mode = str(env_spec.get("mode", "minimal"))
    if mode == "inherit":
        env = dict(os.environ)
    else:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
    extra = env_spec.get("set", {})
    if isinstance(extra, dict):
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def _stdin_payload(shell: dict[str, Any]) -> bytes | None:
    stdin = shell.get("stdin", {})
    if not isinstance(stdin, dict):
        return None
    mode = str(stdin.get("mode", "none"))
    if mode == "none":
        return None
    if mode == "inline":
        text = stdin.get("text", "")
        return str(text).encode("utf-8")
    return None


def execute_shell_action(
    action: dict[str, Any],
    *,
    workspace_uri: str,
) -> CommandResult:
    """Spawn shell.argv without shell interpretation."""
    shell = action.get("shell")
    if not isinstance(shell, dict):
        msg = "shell action missing shell object"
        raise ValueError(msg)
    argv = shell.get("argv")
    if not isinstance(argv, list) or not argv:
        msg = "shell.argv must be a non-empty array"
        raise ValueError(msg)
    command = [str(part) for part in argv]
    cwd = _resolve_cwd(shell, workspace_uri)
    env = _build_env(shell)
    stdin_bytes = _stdin_payload(shell)
    timeout_seconds = int(shell.get("timeout_seconds", 300))
    started_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = time.monotonic()

    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)  # nosec B603
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=stdin_bytes, timeout=timeout_seconds)
        exit_code = proc.returncode
        signal_num = None
    except subprocess.TimeoutExpired:
        timed_out = True
        stdout, stderr = proc.communicate()
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
        else:
            proc.kill()
        proc.wait(timeout=5)
        exit_code = None
        signal_num = None
    ended_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    del start
    return CommandResult(
        exit_code=exit_code,
        signal=signal_num,
        timed_out=timed_out,
        stdout=stdout or b"",
        stderr=stderr or b"",
        started_at=started_at,
        ended_at=ended_at,
    )


def _write_verified_artifact(
    artifact_store: ArtifactStore,
    content: bytes,
    *,
    artifact_id: str,
    media_type: str,
    description: str,
) -> dict[str, Any]:
    """Write an artifact and verify digest; retry once on verification failure."""
    ref = artifact_store.write_bytes(
        content,
        artifact_id=artifact_id,
        media_type=media_type,
        description=description,
    )
    try:
        artifact_store.verify_reference(ref)
        return ref
    except ArtifactIntegrityError:
        ref = artifact_store.write_bytes(
            content,
            artifact_id=artifact_id,
            media_type=media_type,
            description=description,
        )
        artifact_store.verify_reference(ref)
        return ref


def build_action_result(
    command_result: CommandResult,
    *,
    action: dict[str, Any],
    artifact_store: ArtifactStore | None,
    inline_limit: int,
) -> dict[str, Any]:
    """Map a command result to wip.action_result fields."""
    shell = action.get("shell", {})
    expected = shell.get("expected_exit_codes", [0]) if isinstance(shell, dict) else [0]
    if command_result.timed_out:
        status = "timed_out"
        changed = "unknown"
    elif command_result.exit_code in expected:
        status = "completed"
        changed = "unknown"
    else:
        status = "failed"
        changed = "unknown"

    stdout_text = redact_text(command_result.stdout.decode("utf-8", errors="replace"))
    stderr_text = redact_text(command_result.stderr.decode("utf-8", errors="replace"))
    output: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    storage_errors: list[str] = []

    if len(stdout_text.encode("utf-8")) <= inline_limit:
        output["stdout"] = stdout_text
    elif artifact_store is not None:
        try:
            ref = _write_verified_artifact(
                artifact_store,
                stdout_text.encode("utf-8"),
                artifact_id="art_stdout",
                media_type="text/plain",
                description="stdout",
            )
            artifacts.append(ref)
            output["stdout_artifact"] = ref["artifact_id"]
        except ArtifactIntegrityError:
            output["stdout"] = stdout_text[:inline_limit]
            storage_errors.append("artifact storage failed for stdout spillover")

    if len(stderr_text.encode("utf-8")) <= inline_limit:
        output["stderr"] = stderr_text
    elif artifact_store is not None:
        try:
            ref = _write_verified_artifact(
                artifact_store,
                stderr_text.encode("utf-8"),
                artifact_id="art_stderr",
                media_type="text/plain",
                description="stderr",
            )
            artifacts.append(ref)
            output["stderr_artifact"] = ref["artifact_id"]
        except ArtifactIntegrityError:
            output["stderr"] = stderr_text[:inline_limit]
            storage_errors.append("artifact storage failed for stderr spillover")

    if storage_errors:
        note = "; ".join(storage_errors)
        output["stderr"] = f"{output.get('stderr', '')}\n{note}".strip()

    result: dict[str, Any] = {
        "status": status,
        "changed": changed,
        "started_at": command_result.started_at,
        "ended_at": command_result.ended_at,
        "process": {
            "exit_code": command_result.exit_code,
            "signal": command_result.signal,
            "timed_out": command_result.timed_out,
        },
        "output": output,
    }
    if artifacts:
        result["artifacts"] = artifacts
    return result


def noop_action_result() -> dict[str, Any]:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "status": "completed",
        "changed": "no",
        "started_at": now,
        "ended_at": now,
        "process": {"exit_code": 0, "signal": None, "timed_out": False},
        "output": {"stdout": "", "stderr": ""},
    }
