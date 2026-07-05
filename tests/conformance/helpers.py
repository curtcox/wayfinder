"""Shared helpers for Appendix B conformance vectors."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wayfinder.brains.scripted import ScriptedBrain
from wayfinder.cli.jsonrpc import JsonRpcServer
from wayfinder.cli.service import WayfinderService
from wayfinder.core.goal_store import GoalStore


def owner_actor() -> dict[str, str | bool]:
    return {"type": "human", "id": getpass.getuser(), "authority": "owner", "authenticated": True}


def executor_actor(executor_id: str) -> dict[str, str | bool]:
    return {
        "type": "executor",
        "id": executor_id,
        "authority": "operator",
        "authenticated": True,
    }


def goal_create_payload(
    workspace: Path,
    *,
    create_id: str = "create_test_01",
    description: str = "Make the project tests pass.",
) -> dict[str, object]:
    return {
        "schema": "wip.goal_create/0.1",
        "protocol_version": "0.1",
        "create_id": create_id,
        "created_at": "2026-07-04T18:00:00Z",
        "actor": owner_actor(),
        "description": description,
        "workspace_uri": f"file:{workspace}",
        "policy": {"max_auto_risk_level": "low"},
    }


def run_cli(
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


def run_exec(
    args: list[str],
    *,
    store: Path,
    playbook: Path | None = None,
    wayfinder: str | None = None,
    policy: Path | None = None,
    executor_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.exec", "--store", str(store)]
    if playbook is not None:
        cmd.extend(["--brain-playbook", str(playbook)])
    if wayfinder is not None:
        cmd.extend(["--wayfinder", wayfinder])
    if policy is not None:
        cmd.extend(["--policy", str(policy)])
    if executor_id is not None:
        cmd.extend(["--executor-id", executor_id])
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def write_playbook(path: Path, recommendation: dict[str, Any]) -> Path:
    """Write a single-rule scripted playbook and return *path*."""
    payload = {"rules": [{"match": {"goal_status": "pending"}, "recommendation": recommendation}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_exec_playbook(path: Path, recommendation: dict[str, Any]) -> Path:
    """Write a playbook that runs one action then marks the goal done."""
    payload = {
        "rules": [
            {
                "match": {"goal_status": "pending", "open_recommendation_id": {"$null": True}},
                "recommendation": recommendation,
            },
            {
                "match": {
                    "completed_steps": {"$gte": 1},
                    "open_recommendation_id": {"$null": True},
                },
                "recommendation": {
                    "recommendation_type": "done",
                    "summary": "Action finished.",
                    "goal_status": "running",
                    "confidence": 0.95,
                    "done": {"reason": "Action finished."},
                },
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def event_types_from_history(store: Path, goal_id: str) -> list[str]:
    history = run_cli(["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"])
    assert history.returncode == 0, history.stdout + history.stderr
    return [json.loads(line)["type"] for line in history.stdout.splitlines() if line.strip()]


def shell_action_recommendation(
    *,
    argv: list[str],
    workspace: Path,
    action_id: str = "act_01",
    recommendation_id: str = "rec_01",
    preconditions: list[dict[str, Any]] | None = None,
    env_set: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
    expected_exit_codes: list[int] | None = None,
    kind: str = "shell",
) -> dict[str, Any]:
    """Build a minimal executable action recommendation for scripted playbooks."""
    env: dict[str, Any] = {"mode": "minimal", "set": env_set or {}}
    recommendation: dict[str, Any] = {
        "recommendation_type": "action",
        "summary": "Test action.",
        "goal_status": "running",
        "confidence": 0.9,
        "action": {
            "action_id": action_id,
            "kind": kind,
            "title": "Test",
            "shell": {
                "argv": argv,
                "command_for_display": " ".join(argv),
                "cwd": f"file:{workspace}",
                "env": env,
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": timeout_seconds,
                "expected_exit_codes": expected_exit_codes or [0],
                "requires_shell": False,
            },
            "preconditions": preconditions or [],
            "success_criteria": [],
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_test",
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
    }
    recommendation["recommendation_id"] = recommendation_id
    return recommendation


def run_update_via_cli(
    store: Path,
    goal_id: str,
    update: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        ["--store", str(store), "update", "--goal-id", goal_id],
        stdin=json.dumps(update),
    )


def create_goal_via_cli(
    store: Path,
    workspace: Path,
    *,
    create_id: str = "create_test_01",
    description: str = "Make the project tests pass.",
    playbook: Path | None = None,
) -> dict[str, Any]:
    body = json.dumps(goal_create_payload(workspace, create_id=create_id, description=description))
    args = ["--store", str(store), "goal", "create"]
    if playbook is not None:
        args = ["--store", str(store), "--brain-playbook", str(playbook), "goal", "create"]
    proc = run_cli(args, stdin=body)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    result: dict[str, Any] = payload["result"]
    return result


def service_for_store(store: Path, *, playbook: Path | None = None) -> WayfinderService:
    if playbook is not None:
        brain = ScriptedBrain.from_path(playbook)
    else:
        brain = ScriptedBrain.default()
    return WayfinderService(brain=brain, store_root=store)


def issue_recommendation(
    service: WayfinderService,
    goal_id: str,
    *,
    mode: str = "issue",
) -> dict[str, Any]:
    """Issue the next recommendation and return identifiers for follow-up updates."""
    issued = service.next(goal_id, mode=mode)
    history_lines = service.history(goal_id, since_seq=0)
    issued_event = json.loads(history_lines[-1])
    action = issued.get("action", {})
    action_id = action.get("action_id") if isinstance(action, dict) else None
    return {
        "recommendation_id": str(issued["recommendation_id"]),
        "action_id": str(action_id) if action_id is not None else None,
        "issued_event_seq": int(issued_event["seq"]),
        "issued_event_hash": str(issued_event["event_hash"]),
        "recommendation": issued,
    }


def append_observations(service: WayfinderService, goal_id: str, *, count: int) -> None:
    for index in range(count):
        service.update(
            goal_id,
            {
                "schema": "wip.update/0.1",
                "protocol_version": "0.1",
                "update_id": f"upd_obs_{index:03d}",
                "goal_id": goal_id,
                "created_at": f"2026-07-04T18:{index % 60:02d}:00Z",
                "actor": owner_actor(),
                "update_type": "observation",
                "observations": [
                    {"text": f"observation {index}", "effective": {"invalidates": False}},
                ],
            },
        )


def corrupt_event_hash_at(store: Path, goal_id: str, *, seq: int) -> None:
    """Tamper the event_hash at *seq* to simulate log corruption."""
    log_path = store / "goals" / goal_id / "events.ndjson"
    lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    updated: list[str] = []
    for line in lines:
        if not line.strip():
            updated.append(line)
            continue
        event = json.loads(line)
        if int(event["seq"]) == seq:
            event["event_hash"] = "sha256:" + ("0" * 64)
            updated.append(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
        else:
            updated.append(line)
    log_path.write_text("".join(updated), encoding="utf-8")


def seed_expired_recommendation(
    service: WayfinderService,
    goal_id: str,
    workspace: Path,
) -> dict[str, Any]:
    """Append a recommendation whose expires_at is already in the past."""
    goal_store = GoalStore(service._store_root(), goal_id)
    events = goal_store.read_events()
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recommendation = {
        "schema": "wip.recommendation/0.1",
        "protocol_version": "0.1",
        "goal_id": goal_id,
        "recommendation_id": "rec_expired",
        "recommendation_type": "action",
        "summary": "Expired recommendation.",
        "goal_status": "running",
        "confidence": 0.5,
        "executable": True,
        "issued_at": past,
        "expires_at": past,
        "parallel": False,
        "supersedes": [],
        "wayfinder": {"name": "test", "version": "0.1", "instance_id": "test"},
        "basis": {
            "event_log_seq": int(events[-1]["seq"]),
            "event_log_head": str(events[-1]["event_hash"]),
            "state_version": "expired-seed",
        },
        "lease": {"lease_id": "lease_expired", "lease_expires_at": past},
        "action": {
            "action_id": "act_expired",
            "kind": "shell",
            "title": "noop",
            "shell": {
                "argv": ["true"],
                "command_for_display": "true",
                "cwd": f"file:{workspace}",
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
            "key": "idem_expired",
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
        "run_id": None,
    }
    issued_event = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_expired_issue",
        "type": "recommendation.issued",
        "time": past,
        "goal_id": goal_id,
        "source": "wayfinder://test",
        "actor": {"type": "wayfinder", "id": "test", "authority": "operator"},
        "data": {"recommendation": recommendation},
        "run_id": None,
    }
    appended = goal_store.append_events([issued_event], holder="conformance-seed")
    stamped = appended.events[0]
    return {
        "recommendation_id": "rec_expired",
        "action_id": "act_expired",
        "issued_event_seq": int(stamped["seq"]),
        "issued_event_hash": str(stamped["event_hash"]),
    }


def rpc_request(
    server: JsonRpcServer,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: str = "req_1",
) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        request["params"] = params
    response = server.handle_request(request)
    assert response is not None
    return response


def initialize_rpc(server: JsonRpcServer) -> dict[str, Any]:
    return rpc_request(
        server,
        "initialize",
        {"protocol_version": "0.1", "client": {"name": "test", "version": "0.1"}},
        request_id="init_1",
    )
