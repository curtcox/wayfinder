"""Mechanical executor policy evaluation (§8.3)."""

from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from wayfinder.cli.store_paths import parse_workspace_uri

RISK_LEVEL_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

KNOWN_RISK_CLASSES = frozenset(
    {
        "read_local",
        "execute_local",
        "write_workspace",
        "write_host",
        "delete",
        "network_read",
        "network_write",
        "external_side_effect",
        "secrets_access",
        "privileged",
        "cost",
        "privacy",
        "irreversible",
    },
)

DEFAULT_POLICY: dict[str, Any] = {
    "auto_execute": {
        "max_risk_level": "low",
        "allowed_classes": ["read_local", "execute_local", "write_workspace"],
        "denied_classes": [
            "delete",
            "write_host",
            "network_read",
            "network_write",
            "external_side_effect",
            "secrets_access",
            "privileged",
            "cost",
            "irreversible",
        ],
    },
    "approval_required": [
        {"requires_shell": True},
        {"env_mode": "inherit"},
    ],
    "shell": {
        "require_argv": True,
        "allow_requires_shell": False,
        "allow_pty": False,
        "default_env_mode": "minimal",
        "denied_argv0": ["rm", "sudo", "doas", "dd", "mkfs", "shutdown", "reboot"],
    },
}


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of evaluating an action against local policy."""

    denied: bool
    requires_approval: bool
    reason_code: str | None = None
    reason: str | None = None


def _merge_policy_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {**base, **override}
    for list_key in ("denied_argv0", "denied_classes", "allowed_classes"):
        if list_key in override and isinstance(override[list_key], list):
            existing = base.get(list_key, [])
            if isinstance(existing, list):
                merged[list_key] = existing + [
                    item for item in override[list_key] if item not in existing
                ]
    return merged


def _effective_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    effective = copy.deepcopy(DEFAULT_POLICY)
    if not policy:
        return effective
    for key, value in policy.items():
        if isinstance(value, dict) and isinstance(effective.get(key), dict):
            effective[key] = _merge_policy_dict(effective[key], value)
        else:
            effective[key] = value
    return effective


def load_policy(path: Path | None = None) -> dict[str, Any]:
    """Load policy from *path* or baked-in defaults."""
    if path is None:
        config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        for candidate in (
            Path(config_home) / "wayfinder" / "policy.json",
            Path(config_home) / "wayfinder" / "policy.yaml",
        ):
            if candidate.exists():
                path = candidate
                break
    if path is None or not path.exists():
        return copy.deepcopy(DEFAULT_POLICY)
    raw = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            msg = "PyYAML is required to load YAML policy files"
            raise RuntimeError(msg) from exc
        loaded = yaml.safe_load(raw)
    else:
        loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        msg = "policy file must contain a JSON object"
        raise TypeError(msg)
    return _effective_policy(loaded)


def _risk_level_value(level: str) -> int:
    return RISK_LEVEL_ORDER.get(level, 99)


def _resolve_cwd(shell: dict[str, Any], workspace: Path) -> Path:
    cwd_uri = str(shell.get("cwd", f"file:{workspace}"))
    parsed = urlparse(cwd_uri)
    if parsed.scheme != "file":
        msg = f"unsupported cwd scheme: {parsed.scheme}"
        raise ValueError(msg)
    path = Path(unquote(parsed.path)).resolve()
    workspace_resolved = workspace.resolve()
    if path != workspace_resolved and workspace_resolved not in path.parents:
        msg = f"cwd escapes workspace: {path}"
        raise ValueError(msg)
    return path


def _deny(
    reason_code: str,
    reason: str,
    *,
    requires_approval: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        denied=True,
        requires_approval=requires_approval,
        reason_code=reason_code,
        reason=reason,
    )


def _evaluate_shell_policy(
    shell: dict[str, Any],
    shell_policy: dict[str, Any],
) -> PolicyDecision | None:
    argv = shell.get("argv")
    if shell_policy.get("require_argv", True) and (not isinstance(argv, list) or not argv):
        return _deny("invalid_action", "shell.argv is required")
    argv0 = str(argv[0]) if isinstance(argv, list) and argv else ""
    denied_argv0 = shell_policy.get("denied_argv0", [])
    if isinstance(denied_argv0, list) and Path(argv0).name in denied_argv0:
        return _deny("denied_argv0", f"argv[0] denied by policy: {argv0}")
    if shell.get("pty") is True and not shell_policy.get("allow_pty", False):
        return _deny("pty_not_allowed", "pty is not allowed under v0.1 policy")
    if shell.get("requires_shell") is True and not shell_policy.get("allow_requires_shell", False):
        return PolicyDecision(
            denied=True,
            requires_approval=True,
            reason_code="requires_shell",
            reason="requires_shell actions need explicit approval",
        )
    env = shell.get("env", {})
    env_mode = (
        env.get("mode", shell_policy.get("default_env_mode", "minimal"))
        if isinstance(env, dict)
        else "minimal"
    )
    if env_mode == "inherit":
        return PolicyDecision(
            denied=False,
            requires_approval=True,
            reason_code="env_inherit",
            reason="env.mode inherit requires approval",
        )
    return None


def _evaluate_risk_policy(risk: dict[str, Any], auto: dict[str, Any]) -> PolicyDecision | None:
    max_level = str(auto.get("max_risk_level", "low"))
    risk_level = str(risk.get("level", "unknown"))
    if _risk_level_value(risk_level) > _risk_level_value(max_level):
        return _deny(
            "risk_level",
            f"risk level {risk_level} exceeds auto_execute max {max_level}",
        )
    if risk.get("requires_approval") is True:
        return PolicyDecision(
            denied=False,
            requires_approval=True,
            reason_code="requires_approval",
            reason="recommendation risk.requires_approval is true",
        )
    classes = risk.get("classes", [])
    if not isinstance(classes, list):
        classes = []
    denied_classes = auto.get("denied_classes", [])
    allowed_classes = auto.get("allowed_classes", [])
    if not isinstance(denied_classes, list):
        denied_classes = []
    if not isinstance(allowed_classes, list):
        allowed_classes = []
    for risk_class in classes:
        cls = str(risk_class)
        if cls not in KNOWN_RISK_CLASSES:
            return _deny("unknown_risk_class", f"unknown risk class: {cls}")
        if cls in denied_classes or (allowed_classes and cls not in allowed_classes):
            return _deny("denied_risk_class", f"risk class not allowed: {cls}")
    return None


def evaluate_policy(
    action: dict[str, Any],
    risk: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    workspace_uri: str,
) -> PolicyDecision:
    """Evaluate mechanical policy for a shell action."""
    effective = _effective_policy(policy)
    workspace = parse_workspace_uri(workspace_uri)
    kind = str(action.get("kind", ""))
    if kind == "noop":
        return PolicyDecision(denied=False, requires_approval=False)
    if kind != "shell":
        return _deny("unknown_action_kind", f"unsupported action kind: {kind}")

    shell = action.get("shell")
    if not isinstance(shell, dict):
        return _deny("invalid_action", "shell action missing shell object")

    shell_policy = effective.get("shell", {})
    if not isinstance(shell_policy, dict):
        shell_policy = {}
    shell_decision = _evaluate_shell_policy(shell, shell_policy)
    if shell_decision is not None:
        return shell_decision

    auto = effective.get("auto_execute", {})
    if not isinstance(auto, dict):
        auto = {}
    risk_decision = _evaluate_risk_policy(risk, auto)
    if risk_decision is not None:
        return risk_decision

    try:
        _resolve_cwd(shell, workspace)
    except ValueError as exc:
        return _deny("path_containment", str(exc))

    return PolicyDecision(denied=False, requires_approval=False)


def check_preconditions(
    preconditions: list[dict[str, Any]],
    *,
    workspace_uri: str,
    events: list[dict[str, Any]],
    recommendation_id: str,
) -> PolicyDecision:
    """Evaluate supported preconditions; unsupported kinds are blocked."""
    workspace = parse_workspace_uri(workspace_uri)
    for precondition in preconditions:
        kind = str(precondition.get("kind", ""))
        if kind == "path_exists":
            target = precondition.get("path")
            if not isinstance(target, str):
                return PolicyDecision(
                    denied=True,
                    requires_approval=False,
                    reason_code="precondition_failed",
                    reason="path_exists precondition missing path",
                )
            parsed = urlparse(target)
            if parsed.scheme == "file":
                path = Path(unquote(parsed.path))
            else:
                path = Path(target)
            if not path.is_absolute():
                path = workspace / path
            if not path.exists():
                return PolicyDecision(
                    denied=True,
                    requires_approval=False,
                    reason_code="precondition_failed",
                    reason=f"path does not exist: {path}",
                )
            continue
        if kind == "command_available":
            command = precondition.get("command")
            if not isinstance(command, str) or not shutil.which(command):
                return PolicyDecision(
                    denied=True,
                    requires_approval=False,
                    reason_code="precondition_failed",
                    reason=f"command not available: {command}",
                )
            continue
        if kind == "env_present":
            name = precondition.get("name")
            if not isinstance(name, str) or name not in os.environ:
                return PolicyDecision(
                    denied=True,
                    requires_approval=False,
                    reason_code="precondition_failed",
                    reason=f"environment variable missing: {name}",
                )
            continue
        if kind == "approval":
            if not _approval_granted(events, recommendation_id=recommendation_id):
                return PolicyDecision(
                    denied=False,
                    requires_approval=True,
                    reason_code="needs_approval",
                    reason="approval precondition not satisfied",
                )
            continue
        return PolicyDecision(
            denied=True,
            requires_approval=False,
            reason_code="unsupported_precondition",
            reason=f"unsupported precondition kind: {kind}",
        )
    return PolicyDecision(denied=False, requires_approval=False)


def _approval_granted(events: list[dict[str, Any]], *, recommendation_id: str) -> bool:
    for event in events:
        if event.get("type") != "approval.granted":
            continue
        data = event.get("data", {})
        if isinstance(data, dict) and data.get("recommendation_id") == recommendation_id:
            return True
    return False
