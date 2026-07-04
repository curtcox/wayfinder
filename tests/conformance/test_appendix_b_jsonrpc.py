"""Appendix B JSON-RPC conformance vectors."""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import pytest

from tests.conformance.helpers import (
    create_goal_via_cli,
    goal_create_payload,
    initialize_rpc,
    rpc_request,
    run_cli,
    service_for_store,
)
from wayfinder.cli.jsonrpc import JsonRpcServer

pytestmark = pytest.mark.conformance


def test_15_22_jsonrpc_result_shape(tmp_path: Path) -> None:
    """§15.22: JSON-RPC result is the command object, not a wip.response envelope."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]
    server = JsonRpcServer(service_for_store(store))
    initialize_rpc(server)

    response = rpc_request(
        server,
        "goal.status",
        {"goal_id": goal_id},
        request_id="req_01",
    )
    assert "error" not in response
    assert response["id"] == "req_01"
    result = response["result"]
    assert result["schema"] == "wip.status/0.1"
    assert "command" not in result
    assert result.get("schema") != "wip.response/0.1"


def test_15_34_jsonrpc_history_shape(tmp_path: Path) -> None:
    """§15.34: JSON-RPC history matches CLI JSONL event-for-event."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    service.next(goal_id, mode="issue")
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_obs_1",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:05:00Z",
            "actor": {
                "type": "human",
                "id": getpass.getuser(),
                "authority": "owner",
                "authenticated": True,
            },
            "update_type": "observation",
            "observations": [{"text": "note", "effective": {"invalidates": False}}],
        },
    )
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_obs_2",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:06:00Z",
            "actor": {
                "type": "human",
                "id": getpass.getuser(),
                "authority": "owner",
                "authenticated": True,
            },
            "update_type": "observation",
            "observations": [{"text": "note 2", "effective": {"invalidates": False}}],
        },
    )
    service.update(
        goal_id,
        {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": "upd_obs_3",
            "goal_id": goal_id,
            "created_at": "2026-07-04T18:07:00Z",
            "actor": {
                "type": "human",
                "id": getpass.getuser(),
                "authority": "owner",
                "authenticated": True,
            },
            "update_type": "observation",
            "observations": [{"text": "note 3", "effective": {"invalidates": False}}],
        },
    )

    cli_history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    assert cli_history.returncode == 0
    cli_events = [json.loads(line) for line in cli_history.stdout.splitlines() if line.strip()]
    assert len(cli_events) == 5

    server = JsonRpcServer(service_for_store(store))
    initialize_rpc(server)
    response = rpc_request(
        server,
        "goal.history",
        {"goal_id": goal_id, "since_seq": 0},
        request_id="req_9",
    )
    assert response["id"] == "req_9"
    result = response["result"]
    assert result["truncated"] is False
    assert result["next_since_seq"] is None
    assert result["events"] == cli_events


def test_initialize_must_be_first(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    server = JsonRpcServer(service_for_store(store))
    response = rpc_request(server, "goal.status", {"goal_id": "goal_missing"})
    assert response["error"]["code"] == -32000
    assert response["error"]["data"]["error"]["code"] == "invalid_input"
