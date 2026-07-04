"""CLI integration tests."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from pathlib import Path

from wayfinder.brains.scripted import ScriptedBrain
from wayfinder.cli import responses
from wayfinder.cli.service import WayfinderService


def _goal_create_payload(
    workspace: Path, *, create_id: str = "create_test_01"
) -> dict[str, object]:
    return {
        "schema": "wip.goal_create/0.1",
        "protocol_version": "0.1",
        "create_id": create_id,
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "human", "id": getpass.getuser(), "authority": "owner"},
        "description": "Make the project tests pass.",
        "workspace_uri": f"file:{workspace}",
        "policy": {"max_auto_risk_level": "low"},
    }


def _run_cli(
    args: list[str],
    *,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.cli", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        cwd=cwd,
    )


def test_capabilities_envelope() -> None:
    proc = _run_cli(["capabilities", "--request-id", "req_caps"])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "wip.response/0.1"
    assert payload["request_id"] == "req_caps"
    assert payload["result"]["schema"] == "wip.capabilities/0.1"
    assert payload["result"]["features"]["verify"] is True


def test_goal_create_status_and_idempotency(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    body = json.dumps(_goal_create_payload(workspace))
    first = _run_cli(["--store", str(store), "goal", "create"], stdin=body)
    assert first.returncode == 0, first.stderr
    created = json.loads(first.stdout)
    assert created["result"]["replayed"] is False
    goal_id = created["result"]["goal"]["goal_id"]

    second = _run_cli(["--store", str(store), "goal", "create"], stdin=body)
    assert second.returncode == 0, second.stderr
    replayed = json.loads(second.stdout)
    assert replayed["result"]["replayed"] is True
    assert replayed["result"]["goal"]["goal_id"] == goal_id

    status_proc = _run_cli(["--store", str(store), "status", "--goal-id", goal_id])
    assert status_proc.returncode == 0
    status_payload = json.loads(status_proc.stdout)
    assert status_payload["schema"] == "wip.response/0.1"
    assert status_payload["result"]["schema"] == "wip.status/0.1"
    assert status_payload["result"]["goal_status"] == "pending"


def test_next_preview_and_issue(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = WayfinderService(brain=ScriptedBrain.default(), store_root=store)
    created = service.goal_create(_goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])

    preview = service.next(goal_id, mode="preview", explain_mode="summary")
    assert preview["executable"] is False
    assert preview["recommendation_type"] == "action"
    assert preview["action"]["shell"]["argv"] == ["make", "test"]

    issued = service.next(goal_id, mode="issue")
    assert issued["executable"] is True
    assert issued["lease"] is not None

    conflict_proc = _run_cli(
        ["--store", str(store), "next", "--goal-id", goal_id, "--mode=issue"],
    )
    assert conflict_proc.returncode == 2
    error = json.loads(conflict_proc.stdout)
    assert error["schema"] == "wip.error/0.1"
    assert error["error"]["code"] == "storage_conflict"


def test_history_streams_jsonl(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = WayfinderService(brain=ScriptedBrain.default(), store_root=store)
    created = service.goal_create(_goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])

    proc = _run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    assert proc.returncode == 0
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "goal.created"


def test_verify_reports_ok(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = WayfinderService(brain=ScriptedBrain.default(), store_root=store)
    created = service.goal_create(_goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])

    proc = _run_cli(["--store", str(store), "verify", "--goal-id", goal_id])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["result"]["ok"] is True


def test_error_exit_codes_match_spec() -> None:
    assert responses.exit_code_for_error("invalid_input") == 1
    assert responses.exit_code_for_error("storage_conflict") == 2
    assert responses.exit_code_for_error("stale_recommendation") == 5
    assert responses.exit_code_for_error("policy_denied") == 7
