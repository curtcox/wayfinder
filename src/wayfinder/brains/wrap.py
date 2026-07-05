"""Tool-specialized wrap brain for wayfinder-wrap."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from typing import Any

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig, load_llm_config
from wayfinder.llm.structured import generate_brain_recommendation

NETWORK_TOOLS = frozenset(
    {
        "curl",
        "wget",
        "http",
        "httpie",
        "gh",
        "aws",
        "kubectl",
        "gcloud",
        "az",
        "terraform",
        "ansible-playbook",
    },
)
NETWORK_READ_CLASSES = frozenset({"network_read", "write_workspace"})
NETWORK_WRITE_CLASSES = frozenset({"network_write", "external_side_effect", "write_workspace"})


def harvest_tool_help(tool: str) -> str:
    """Capture `--help` output for *tool*, if available on PATH."""
    if shutil.which(tool) is None:
        return f"{tool} is not available on PATH."
    for args in (["--help"], ["help"], ["-h"]):
        try:
            proc = subprocess.run(  # nosec B603 B607
                [tool, *args],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = proc.stdout.strip() or proc.stderr.strip()
        if output:
            return output[:12000]
    return f"{tool} did not produce help output."


def _wrap_system_prompt(tool: str, tool_help: str) -> str:
    return (
        f"You are wayfinder-wrap for `{tool}`. Your expertise is driving `{tool}` to achieve "
        "user goals through WIP v0.1 shell actions. Recommend concrete, fully-specified "
        f"`{tool}` invocations as shell actions with argv starting with `{tool}`. "
        "Use the workspace_uri as shell.cwd unless the goal requires otherwise. "
        "Emit one recommendation JSON object without issuance metadata. "
        "Tool reference:\n"
        f"{tool_help}"
    )


def _looks_like_network_write(tool: str, argv: list[str]) -> bool:
    if tool not in NETWORK_TOOLS:
        return False
    joined = " ".join(argv).lower()
    write_markers = (" -x ", " -d ", " --data", " post ", " put ", " delete ", " patch ")
    mutating_subcommands = ("create", "delete", "apply", "rm ", "remove", "release")
    return any(marker in joined for marker in write_markers) or any(
        token in joined for token in mutating_subcommands
    )


def enforce_wrap_risk(recommendation: dict[str, Any], *, tool: str) -> dict[str, Any]:
    """Mechanically ensure wrapped tools carry honest network risk metadata."""
    if recommendation.get("recommendation_type") != "action":
        return recommendation
    action = recommendation.get("action")
    if not isinstance(action, dict):
        return recommendation
    shell = action.get("shell")
    if not isinstance(shell, dict):
        return recommendation
    argv = shell.get("argv")
    if not isinstance(argv, list) or not argv:
        return recommendation

    risk = recommendation.setdefault("risk", {})
    if not isinstance(risk, dict):
        return recommendation

    if tool not in NETWORK_TOOLS and str(argv[0]) not in NETWORK_TOOLS:
        return recommendation

    classes = risk.setdefault("classes", [])
    if not isinstance(classes, list):
        classes = []
        risk["classes"] = classes

    network_write = _looks_like_network_write(tool, [str(item) for item in argv])
    required = NETWORK_WRITE_CLASSES if network_write else NETWORK_READ_CLASSES
    for class_name in required:
        if class_name not in classes:
            classes.append(class_name)

    risk["network"] = "required"
    risk["requires_approval"] = True
    if network_write:
        risk["level"] = "high" if risk.get("level") == "low" else risk.get("level", "medium")
        risk["destructive"] = bool(risk.get("destructive", True))
    else:
        risk["level"] = risk.get("level", "medium")
    return recommendation


class WrapBrain:
    """LLM brain specialized for one command-line tool."""

    def __init__(self, tool: str, client: ChatClient, *, tool_help: str | None = None) -> None:
        self._tool = tool
        self._client = client
        self._tool_help = tool_help if tool_help is not None else harvest_tool_help(tool)

    @classmethod
    def from_config(cls, tool: str, config: LLMConfig | None = None) -> WrapBrain:
        resolved = config or load_llm_config()
        return cls(tool, ChatClient(resolved))

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        del mode
        user_payload = {
            "tool": self._tool,
            "goal": goal,
            "status": status,
            "recent_events": events[-20:],
            "explain_mode": explain_mode,
        }
        messages = [
            {"role": "system", "content": _wrap_system_prompt(self._tool, self._tool_help)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        recommendation = generate_brain_recommendation(self._client, messages)
        recommendation = enforce_wrap_risk(recommendation, tool=self._tool)
        if explain_mode == "none":
            recommendation.pop("explanation", None)
        return recommendation
