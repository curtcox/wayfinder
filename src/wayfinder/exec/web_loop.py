"""Executor loop variant that supports browser step actions (§9.10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder.exec.loop import ExecutorConfig, ExecutorLoop
from wayfinder.exec.shell_exec import build_action_result, execute_shell_action, noop_action_result
from wayfinder.exec.web_exec import build_web_action_result, execute_web_action


class WebExecutorLoop(ExecutorLoop):
    """wayfinder-exec-web: opt-in browser execution beyond strict v0.1 policy."""

    def __init__(self, config: ExecutorConfig, *, secrets_path: Path | None = None) -> None:
        super().__init__(config)
        shell = dict(self._policy.get("shell", {}))
        shell["allow_browser_steps"] = True
        auto = dict(self._policy.get("auto_execute", {}))
        denied = [
            class_name
            for class_name in auto.get("denied_classes", [])
            if class_name not in {"network_read", "network_write", "external_side_effect"}
        ]
        allowed = list(auto.get("allowed_classes", []))
        for class_name in ("network_read", "network_write", "external_side_effect"):
            if class_name not in allowed:
                allowed.append(class_name)
        auto = {**auto, "denied_classes": denied, "allowed_classes": allowed}
        self._policy = {**self._policy, "shell": shell, "auto_execute": auto}
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
        if isinstance(shell, dict) and isinstance(shell.get("x_browser_steps"), list):
            command_result = execute_web_action(
                action,
                workspace_uri=self._workspace_uri,
                secrets_path=self._secrets_path,
            )
            return build_web_action_result(
                command_result,
                action=action,
                artifact_store=self._artifact_store,
                inline_limit=self._inline_limit,
                workspace_uri=self._workspace_uri,
            )

        command_result = execute_shell_action(action, workspace_uri=self._workspace_uri)
        return build_action_result(
            command_result,
            action=action,
            artifact_store=self._artifact_store,
            inline_limit=self._inline_limit,
        )
