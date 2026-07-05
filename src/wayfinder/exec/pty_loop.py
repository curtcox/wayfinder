"""Executor loop variant that supports pty dialogue actions (§9.8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder.exec.loop import ExecutorConfig, ExecutorLoop
from wayfinder.exec.pty_exec import build_pty_action_result, execute_pty_action
from wayfinder.exec.shell_exec import build_action_result, execute_shell_action, noop_action_result


class PtyExecutorLoop(ExecutorLoop):
    """wayfinder-exec-pty: opt-in pty execution beyond strict v0.1 policy."""

    def __init__(self, config: ExecutorConfig, *, secrets_path: Path | None = None) -> None:
        super().__init__(config)
        shell = dict(self._policy.get("shell", {}))
        shell["allow_pty"] = True
        self._policy = {**self._policy, "shell": shell}
        self._secrets_path = secrets_path

    def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("kind", ""))
        if kind == "noop":
            return noop_action_result()
        if kind != "shell":
            from wayfinder.exec.wayfinder_client import WayfinderClientError

            msg = f"unsupported action kind: {kind}"
            raise WayfinderClientError(msg)

        shell = action.get("shell", {})
        if isinstance(shell, dict) and shell.get("pty") is True:
            command_result = execute_pty_action(
                action,
                workspace_uri=self._workspace_uri,
                secrets_path=self._secrets_path,
            )
            return build_pty_action_result(
                command_result,
                action=action,
                artifact_store=self._artifact_store,
                inline_limit=self._inline_limit,
            )

        command_result = execute_shell_action(action, workspace_uri=self._workspace_uri)
        return build_action_result(
            command_result,
            action=action,
            artifact_store=self._artifact_store,
            inline_limit=self._inline_limit,
        )
