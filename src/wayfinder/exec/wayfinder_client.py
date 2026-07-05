"""Subprocess client for invoking a wayfinder CLI."""

from __future__ import annotations

import json
import shlex
import subprocess  # nosec B404
import sys
from typing import Any

from wayfinder.core.errors import InvalidInputError


class WayfinderClientError(RuntimeError):
    """Raised when a wayfinder subprocess returns an error envelope."""


class WayfinderClient:
    """Invoke wayfinder commands and parse wip.response envelopes."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        store: str | None = None,
        brain_playbook: str | None = None,
    ) -> None:
        base = command or [sys.executable, "-m", "wayfinder.cli"]
        self._base = list(base)
        self._store = store
        self._brain_playbook = brain_playbook

    def _args(self, subcommand: list[str]) -> list[str]:
        args = list(self._base)
        if self._store:
            args.extend(["--store", self._store])
        if self._brain_playbook:
            args.extend(["--brain-playbook", self._brain_playbook])
        args.extend(subcommand)
        return args

    def _run(self, subcommand: list[str], *, stdin: str | None = None) -> dict[str, Any]:
        proc = subprocess.run(  # nosec B603
            self._args(subcommand),
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )
        if not proc.stdout.strip():
            msg = f"wayfinder produced no output: {proc.stderr.strip()}"
            raise WayfinderClientError(msg)
        payload = json.loads(proc.stdout)
        if payload.get("schema") == "wip.error/0.1":
            error = payload.get("error", {})
            code = (
                error.get("code", "internal_error") if isinstance(error, dict) else "internal_error"
            )
            message = (
                error.get("message", "wayfinder error")
                if isinstance(error, dict)
                else "wayfinder error"
            )
            msg = f"{code}: {message}"
            raise WayfinderClientError(msg)
        if proc.returncode != 0:
            msg = f"wayfinder exited {proc.returncode}: {proc.stdout}"
            raise WayfinderClientError(msg)
        result = payload.get("result")
        if not isinstance(result, dict):
            msg = "wayfinder response missing result object"
            raise InvalidInputError(msg)
        return result

    def capabilities(self) -> dict[str, Any]:
        return self._run(["capabilities", "--format=json"])

    def status(self, goal_id: str) -> dict[str, Any]:
        return self._run(["status", "--goal-id", goal_id, "--format=json"])

    def next(
        self,
        goal_id: str,
        *,
        mode: str,
        explain: str = "structured",
        supersede: bool = False,
    ) -> dict[str, Any]:
        args = [
            "next",
            "--goal-id",
            goal_id,
            f"--mode={mode}",
            f"--explain={explain}",
            "--format=json",
        ]
        if supersede:
            args.append("--supersede")
        return self._run(args)

    def update(self, goal_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._run(
            ["update", "--goal-id", goal_id, "--format=json"],
            stdin=json.dumps(body),
        )

    def verify(self, goal_id: str) -> dict[str, Any]:
        return self._run(["verify", "--goal-id", goal_id, "--format=json"])

    def history(self, goal_id: str, *, since_seq: int = 0) -> list[dict[str, Any]]:
        proc = subprocess.run(  # nosec B603
            self._args(
                [
                    "history",
                    "--goal-id",
                    goal_id,
                    "--since-seq",
                    str(since_seq),
                    "--format=jsonl",
                ],
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            msg = f"history failed: {proc.stdout}{proc.stderr}"
            raise WayfinderClientError(msg)
        events: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    events.append(parsed)
        return events


def parse_wayfinder_command(value: str) -> list[str]:
    """Split a --wayfinder command string into argv."""
    parts = shlex.split(value)
    if not parts:
        msg = "--wayfinder must name a non-empty command"
        raise InvalidInputError(msg)
    return parts
