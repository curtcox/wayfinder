"""wayfinder-exec-web CLI integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, run_cli


def _web_policy_path(tmp_path: Path) -> Path:
    policy = {
        "auto_execute": {
            "max_risk_level": "medium",
            "allowed_classes": [
                "read_local",
                "execute_local",
                "write_workspace",
                "network_read",
                "network_write",
                "external_side_effect",
            ],
            "denied_classes": [
                "delete",
                "write_host",
                "secrets_access",
                "privileged",
                "cost",
                "irreversible",
            ],
        },
        "shell": {
            "allow_browser_steps": True,
        },
    }
    path = tmp_path / "web_policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def _run_exec_web(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.exec_web", *args]
    env = {**os.environ, "WAYFINDER_WEB_STUB": "1"}
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_exec_web_dry_run_scripted_browser_action(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace)
    payload["metadata"] = {
        "web_steps": [
            {
                "title": "Download invoice",
                "steps": [
                    {"op": "navigate", "url": "https://vendor.example/billing"},
                    {"op": "await_download", "filename": "june.pdf"},
                ],
            },
        ],
    }
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    policy_path = _web_policy_path(tmp_path)
    dry_run = _run_exec_web(
        [
            "--store",
            str(store),
            "--policy",
            str(policy_path),
            "--wayfinder",
            f"{sys.executable} -m wayfinder.web",
            "dry-run",
            "--goal-id",
            goal_id,
        ],
    )
    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    body = json.loads(dry_run.stdout)
    assert body["result"]["extensions"]["browser"] is True
    recommendation = body["result"]["recommendation"]
    assert recommendation["recommendation_type"] == "action"


def test_exec_web_stub_download(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace)
    payload["metadata"] = {
        "web_steps": [
            {
                "title": "Download invoice",
                "steps": [{"op": "await_download", "filename": "june.pdf"}],
            },
        ],
    }
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]

    policy_path = _web_policy_path(tmp_path)
    run_result = _run_exec_web(
        [
            "--store",
            str(store),
            "--policy",
            str(policy_path),
            "--wayfinder",
            f"{sys.executable} -m wayfinder.web",
            "run",
            "--goal-id",
            goal_id,
        ],
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert (workspace / "june.pdf").is_file()
