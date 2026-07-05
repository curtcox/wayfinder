"""Phase 8 deference integration tests (§10)."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from tests.conformance.helpers import (
    create_goal_via_cli,
    run_cli,
    run_exec,
    write_exec_playbook,
)

FIXTURES = Path(__file__).parent / "exec" / "fixtures"
SUB_PLAYBOOK = FIXTURES / "true_playbook.json"


def _wayfinder_cmd(*, playbook: Path) -> str:
    return shlex.join(
        [sys.executable, "-m", "wayfinder.cli", "--brain-playbook", str(playbook)],
    )


def _delegation_argv(
    *,
    sub_store: Path,
    sub_goal_file: Path,
    sub_playbook: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "wayfinder.exec",
        "--wayfinder",
        _wayfinder_cmd(playbook=sub_playbook),
        "--store",
        str(sub_store),
        "run",
        "--goal-file",
        str(sub_goal_file),
    ]


def _write_delegation_playbook(
    path: Path,
    *,
    workspace_uri: str,
    argv: list[str],
    timeout_seconds: int = 300,
) -> Path:
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": "Delegate work to a sub-wayfinder.",
        "goal_status": "running",
        "confidence": 0.9,
        "action": {
            "kind": "shell",
            "title": "Run sub-goal via wayfinder-exec",
            "shell": {
                "argv": argv,
                "command_for_display": "wayfinder-exec run (sub-goal)",
                "cwd": workspace_uri,
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": timeout_seconds,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
            "preconditions": [],
            "success_criteria": [
                {"id": "succ_01", "kind": "exit_code", "operator": "in", "value": [0]},
            ],
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_delegate_sub",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": False,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 1,
        },
        "risk": {
            "level": "low",
            "classes": ["read_local", "execute_local", "write_workspace"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
    }
    return write_exec_playbook(path, recommendation)


def _event_types(store: Path, goal_id: str) -> list[str]:
    proc = run_cli(["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [json.loads(line)["type"] for line in proc.stdout.splitlines() if line.strip()]


def test_deference_sub_store_is_independently_auditable(tmp_path: Path) -> None:
    workspace = tmp_path / "release"
    workspace.mkdir()
    parent_store = tmp_path / "parent-store"
    sub_store = tmp_path / "sub-store"

    sub_created = create_goal_via_cli(sub_store, workspace, description="Generate changelog")
    sub_goal_id = sub_created["goal"]["goal_id"]
    sub_goal_file = tmp_path / "subgoal.txt"
    sub_goal_file.write_text(sub_goal_id, encoding="utf-8")

    parent_playbook = _write_delegation_playbook(
        tmp_path / "parent_playbook.json",
        workspace_uri=f"file:{workspace}",
        argv=_delegation_argv(
            sub_store=sub_store,
            sub_goal_file=sub_goal_file,
            sub_playbook=SUB_PLAYBOOK,
        ),
    )
    parent_created = create_goal_via_cli(
        parent_store,
        workspace,
        description="Cut and publish release 2.4.0",
        playbook=parent_playbook,
    )
    parent_goal_id = parent_created["goal"]["goal_id"]

    proc = run_exec(
        ["run", "--goal-id", parent_goal_id],
        store=parent_store,
        playbook=parent_playbook,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)["result"]
    assert result["stopped_reason"] in {"goal_completed", "done_recommendation"}

    parent_types = _event_types(parent_store, parent_goal_id)
    assert "action.completed" in parent_types

    sub_types = _event_types(sub_store, sub_goal_id)
    assert "action.completed" in sub_types
    assert "goal.completed" in sub_types


def test_deference_inner_policy_blocks_network_laundering(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_store = tmp_path / "parent-store"
    sub_store = tmp_path / "sub-store"

    curl_playbook = write_exec_playbook(
        tmp_path / "curl_playbook.json",
        {
            "recommendation_type": "action",
            "summary": "Fetch remote status.",
            "goal_status": "running",
            "confidence": 0.9,
            "action": {
                "kind": "shell",
                "title": "curl example.com",
                "shell": {
                    "argv": ["curl", "-fsS", "https://example.com/status"],
                    "command_for_display": "curl -fsS https://example.com/status",
                    "cwd": "{workspace_uri}",
                    "env": {"mode": "minimal", "set": {}},
                    "stdin": {"mode": "none"},
                    "pty": False,
                    "timeout_seconds": 30,
                    "expected_exit_codes": [0],
                    "requires_shell": False,
                },
                "preconditions": [],
                "success_criteria": [],
            },
            "idempotency": {
                "level": "strong",
                "key": "idem_{goal_id}_curl",
                "scope": "workspace",
                "safe_to_retry": True,
                "safe_to_run_if_already_done": True,
                "detects_noop": False,
                "dedupe_strategy": "idempotency_key",
                "partial_failure_recovery": "retry",
                "max_attempts": 1,
            },
            "risk": {
                "level": "low",
                "classes": ["network_read"],
                "blast_radius": "workspace",
                "requires_approval": False,
                "destructive": False,
                "network": "required",
                "secrets": "not_required",
                "rollback": {"available": False, "kind": "unknown", "instructions": None},
            },
        },
    )

    sub_created = create_goal_via_cli(sub_store, workspace, description="Publish release")
    sub_goal_id = sub_created["goal"]["goal_id"]
    sub_goal_file = tmp_path / "subgoal.txt"
    sub_goal_file.write_text(sub_goal_id, encoding="utf-8")

    parent_playbook = _write_delegation_playbook(
        tmp_path / "parent_playbook.json",
        workspace_uri=f"file:{workspace}",
        argv=_delegation_argv(
            sub_store=sub_store,
            sub_goal_file=sub_goal_file,
            sub_playbook=curl_playbook,
        ),
    )
    parent_created = create_goal_via_cli(
        parent_store,
        workspace,
        description="Release with delegated publish",
        playbook=parent_playbook,
    )
    parent_goal_id = parent_created["goal"]["goal_id"]

    proc = run_exec(
        ["run", "--goal-id", parent_goal_id],
        store=parent_store,
        playbook=parent_playbook,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    sub_types = _event_types(sub_store, sub_goal_id)
    assert "executor.policy_denied" in sub_types
    assert "action.completed" not in sub_types
    assert "action.started" not in sub_types


def test_deference_timeout_bounds_sub_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_store = tmp_path / "parent-store"
    sub_store = tmp_path / "sub-store"

    sleep_playbook = write_exec_playbook(
        tmp_path / "sleep_playbook.json",
        {
            "recommendation_type": "action",
            "summary": "Sleep longer than the parent timeout.",
            "goal_status": "running",
            "confidence": 0.9,
            "action": {
                "kind": "shell",
                "title": "sleep",
                "shell": {
                    "argv": ["sleep", "5"],
                    "command_for_display": "sleep 5",
                    "cwd": "{workspace_uri}",
                    "env": {"mode": "minimal", "set": {}},
                    "stdin": {"mode": "none"},
                    "pty": False,
                    "timeout_seconds": 60,
                    "expected_exit_codes": [0],
                    "requires_shell": False,
                },
                "preconditions": [],
                "success_criteria": [],
            },
            "idempotency": {
                "level": "strong",
                "key": "idem_{goal_id}_sleep",
                "scope": "workspace",
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
                "rollback": {"available": False, "kind": "unknown", "instructions": None},
            },
        },
    )

    sub_created = create_goal_via_cli(sub_store, workspace)
    sub_goal_id = sub_created["goal"]["goal_id"]
    sub_goal_file = tmp_path / "subgoal.txt"
    sub_goal_file.write_text(sub_goal_id, encoding="utf-8")

    parent_playbook = _write_delegation_playbook(
        tmp_path / "parent_playbook.json",
        workspace_uri=f"file:{workspace}",
        argv=_delegation_argv(
            sub_store=sub_store,
            sub_goal_file=sub_goal_file,
            sub_playbook=sleep_playbook,
        ),
        timeout_seconds=1,
    )
    parent_created = create_goal_via_cli(
        parent_store,
        workspace,
        description="Delegate with a short timeout",
        playbook=parent_playbook,
    )
    parent_goal_id = parent_created["goal"]["goal_id"]

    proc = run_exec(
        ["run", "--goal-id", parent_goal_id],
        store=parent_store,
        playbook=parent_playbook,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    parent_types = _event_types(parent_store, parent_goal_id)
    assert "action.timed_out" in parent_types
    assert "action.completed" not in parent_types
