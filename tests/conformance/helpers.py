"""Shared helpers for Appendix B conformance vectors."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from wayfinder.brains.scripted import ScriptedBrain
from wayfinder.cli.jsonrpc import JsonRpcServer
from wayfinder.cli.service import WayfinderService


def _owner_actor() -> dict[str, str]:
    return {"type": "human", "id": getpass.getuser(), "authority": "owner"}


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
        "actor": _owner_actor(),
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


def create_goal_via_cli(
    store: Path,
    workspace: Path,
    *,
    create_id: str = "create_test_01",
    description: str = "Make the project tests pass.",
) -> dict[str, Any]:
    body = json.dumps(goal_create_payload(workspace, create_id=create_id, description=description))
    proc = run_cli(["--store", str(store), "goal", "create"], stdin=body)
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
