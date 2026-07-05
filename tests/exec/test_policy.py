"""Policy engine tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wayfinder.exec.policy import PolicyDecision, evaluate_policy


def _shell_action(
    *,
    argv: list[str],
    workspace: Path,
    requires_shell: bool = False,
    pty: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "shell",
        "shell": {
            "argv": argv,
            "cwd": f"file:{workspace}",
            "env": {"mode": "minimal", "set": {}},
            "requires_shell": requires_shell,
            "pty": pty,
        },
    }


def _low_local_risk() -> dict[str, Any]:
    return {
        "level": "low",
        "classes": ["execute_local"],
        "requires_approval": False,
    }


def test_denied_argv0_blocks_rm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        _shell_action(argv=["rm", "-rf", "/"], workspace=workspace),
        _low_local_risk(),
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.reason_code == "denied_argv0"


def test_network_class_denied_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        _shell_action(argv=["curl", "https://example.com"], workspace=workspace),
        {"level": "low", "classes": ["network_read"], "requires_approval": False},
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.reason_code == "denied_risk_class"


def test_pty_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        _shell_action(argv=["true"], workspace=workspace, pty=True),
        _low_local_risk(),
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.reason_code == "pty_not_allowed"


def test_browser_steps_rejected_without_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    action = _shell_action(argv=["python", "-m", "wayfinder.web.runner"], workspace=workspace)
    action["shell"]["x_browser_steps"] = [{"op": "navigate", "url": "https://example.com"}]
    decision = evaluate_policy(
        action,
        {"level": "low", "classes": ["network_read"], "requires_approval": False},
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.reason_code == "browser_steps_not_allowed"


def test_requires_shell_needs_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        _shell_action(argv=["bash", "-c", "true"], workspace=workspace, requires_shell=True),
        _low_local_risk(),
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.requires_approval is True


def test_low_execute_local_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        _shell_action(argv=["true"], workspace=workspace),
        _low_local_risk(),
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision == PolicyDecision(denied=False, requires_approval=False)


def test_sensitive_env_value_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    action = _shell_action(argv=["true"], workspace=workspace)
    action["shell"]["env"]["set"] = {"API_KEY": {"value": "secret", "sensitive": True}}
    decision = evaluate_policy(
        action,
        _low_local_risk(),
        policy={},
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is True
    assert decision.reason_code == "sensitive_env_value"


def test_noop_action_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_policy(
        {"kind": "noop"},
        _low_local_risk(),
        workspace_uri=f"file:{workspace}",
    )
    assert decision == PolicyDecision(denied=False, requires_approval=False)
