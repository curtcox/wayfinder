"""Pexpect executor unit tests."""

from __future__ import annotations

import os
import pty
from pathlib import Path

import pytest

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.exec.policy import evaluate_policy, load_policy
from wayfinder.exec.pty_exec import build_pty_action_result, execute_pty_action
from wayfinder.exec.secrets import resolve_secret_ref

pytestmark = pytest.mark.usefixtures("_require_pty_device")


@pytest.fixture(scope="module")
def _require_pty_device() -> None:
    try:
        master, _ = pty.openpty()
    except OSError as exc:
        pytest.skip(f"pty device unavailable: {exc}")
    else:
        os.close(master)


def _login_script() -> str:
    return str(Path(__file__).parent / "fixtures" / "login_prompt.py")


def test_resolve_secret_ref_from_toml(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.toml"
    secrets_path.write_text(
        '[vendor]\nsvc-deploy = "s3cr3t"\n',
        encoding="utf-8",
    )
    assert resolve_secret_ref("vendor/svc-deploy", secrets_path=secrets_path) == "s3cr3t"


def test_pty_policy_allowed_when_opted_in(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = load_policy(None)
    policy = {**policy, "shell": {**policy.get("shell", {}), "allow_pty": True}}
    action = {
        "kind": "shell",
        "shell": {
            "argv": ["vendor-cli", "login"],
            "cwd": f"file:{workspace}",
            "pty": True,
            "x_expect_dialogue": [{"expect": "Username:", "send": "user"}],
        },
    }
    risk = {
        "level": "low",
        "classes": ["execute_local"],
        "requires_approval": False,
    }
    decision = evaluate_policy(
        action,
        risk,
        policy=policy,
        workspace_uri=f"file:{workspace}",
    )
    assert decision.denied is False


def test_execute_pty_action_drives_dialogue(tmp_path: Path) -> None:
    pytest.importorskip("pexpect")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secrets_path = tmp_path / "secrets.toml"
    secrets_path.write_text('[vendor]\nsvc-deploy = "hunter2"\n', encoding="utf-8")
    action = {
        "kind": "shell",
        "shell": {
            "argv": ["python3", _login_script()],
            "cwd": f"file:{workspace}",
            "env": {"mode": "minimal", "set": {}},
            "pty": True,
            "timeout_seconds": 30,
            "expected_exit_codes": [0],
            "x_expect_dialogue": [
                {"expect": "Username:", "send": "svc-deploy"},
                {"expect": "Password:", "send_secret_ref": "vendor/svc-deploy"},
                {"expect": "Session established", "then": "eof"},
            ],
        },
    }
    result = execute_pty_action(
        action,
        workspace_uri=f"file:{workspace}",
        secrets_path=secrets_path,
    )
    transcript = result.stdout.decode("utf-8")
    assert "Session established" in transcript
    assert "hunter2" not in transcript
    assert "[REDACTED]" in transcript
    assert result.exit_code == 0
    assert result.timed_out is False


def test_build_pty_action_result_stores_transcript_artifact(tmp_path: Path) -> None:
    pytest.importorskip("pexpect")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secrets_path = tmp_path / "secrets.toml"
    secrets_path.write_text('[vendor]\nsvc-deploy = "topsecret"\n', encoding="utf-8")
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    command_result = execute_pty_action(
        {
            "kind": "shell",
            "shell": {
                "argv": ["python3", _login_script()],
                "cwd": f"file:{workspace}",
                "env": {"mode": "minimal", "set": {}},
                "pty": True,
                "timeout_seconds": 30,
                "expected_exit_codes": [0],
                "x_expect_dialogue": [
                    {"expect": "Username:", "send": "svc-deploy"},
                    {"expect": "Password:", "send_secret_ref": "vendor/svc-deploy"},
                    {"expect": "Session established", "then": "eof"},
                ],
            },
        },
        workspace_uri=f"file:{workspace}",
        secrets_path=secrets_path,
    )
    action_result = build_pty_action_result(
        command_result,
        action={"kind": "shell", "shell": {"expected_exit_codes": [0]}},
        artifact_store=store,
        inline_limit=100,
    )
    assert action_result["status"] == "completed"
    assert "pty_transcript_artifact" in action_result["output"]
    assert "artifacts" in action_result
    artifact_ref = action_result["artifacts"][0]
    stored_path = store.resolve_uri(str(artifact_ref["uri"]))
    stored = stored_path.read_text(encoding="utf-8")
    assert "topsecret" not in stored
    assert "Session established" in stored
