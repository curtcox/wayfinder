"""wayfinder-exec-pty CLI smoke tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance.helpers import goal_create_payload, run_cli

_LOGIN_SCRIPT = Path(__file__).resolve().parent / "exec" / "fixtures" / "login_prompt.py"


def _run_exec_pty(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.exec_pty", *args]
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def test_exec_pty_dry_run_allows_pty_action(tmp_path: Path) -> None:
    pytest.importorskip("pexpect")
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = tmp_path / "pty_playbook.json"
    playbook.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "match": {
                            "goal_status": "pending",
                            "open_recommendation_id": {"$null": True},
                        },
                        "recommendation": {
                            "recommendation_type": "action",
                            "summary": "Login via pty",
                            "goal_status": "running",
                            "confidence": 0.9,
                            "executable": True,
                            "action": {
                                "kind": "shell",
                                "title": "Vendor login",
                                "shell": {
                                    "argv": ["python3", str(_LOGIN_SCRIPT)],
                                    "command_for_display": "vendor-cli login",
                                    "cwd": "{workspace_uri}",
                                    "env": {"mode": "minimal", "set": {}},
                                    "stdin": {"mode": "none"},
                                    "pty": True,
                                    "timeout_seconds": 30,
                                    "expected_exit_codes": [0],
                                    "requires_shell": False,
                                    "x_expect_dialogue": [
                                        {"expect": "Username:", "send": "svc-deploy"},
                                        {"expect": "Password:", "send": "local-pass"},
                                        {"expect": "Session established", "then": "eof"},
                                    ],
                                },
                                "preconditions": [],
                                "success_criteria": [],
                            },
                            "idempotency": {
                                "level": "strong",
                                "key": "idem_pty_login",
                                "scope": "goal",
                                "safe_to_retry": True,
                                "safe_to_run_if_already_done": True,
                                "detects_noop": False,
                                "dedupe_strategy": "idempotency_key",
                                "partial_failure_recovery": "retry",
                                "max_attempts": 1,
                            },
                            "risk": {
                                "level": "low",
                                "classes": ["execute_local"],
                                "blast_radius": "workspace",
                                "requires_approval": False,
                                "destructive": False,
                                "network": "not_required",
                                "secrets": "not_required",
                                "rollback": {
                                    "available": False,
                                    "kind": "unknown",
                                    "instructions": None,
                                },
                            },
                        },
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    payload = goal_create_payload(workspace)
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    proc = _run_exec_pty(
        [
            "--store",
            str(store),
            "--brain-playbook",
            str(playbook),
            "dry-run",
            "--goal-id",
            goal_id,
        ],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = json.loads(proc.stdout)
    assert body["result"]["stopped_reason"] == "dry_run"
    assert body["result"]["extensions"]["pty"] is True
