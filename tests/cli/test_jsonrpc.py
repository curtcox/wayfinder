"""JSON-RPC server unit tests."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.conformance.helpers import (
    goal_create_payload,
    initialize_rpc,
    rpc_request,
    service_for_store,
)
from wayfinder.cli.jsonrpc import JsonRpcServer, run_jsonrpc_server


def test_shutdown_ends_server(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    server = JsonRpcServer(service)
    initialize_rpc(server)
    response = rpc_request(server, "shutdown")
    assert response["result"] is None
    assert server._running is False


def test_jsonrpc_stdio_subprocess(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    body = goal_create_payload(workspace)
    stdin = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocol_version": "0.1",
                        "client": {"name": "test", "version": "0.1"},
                    },
                    "id": "init",
                },
            ),
            json.dumps({"jsonrpc": "2.0", "method": "goal.create", "params": body, "id": "create"}),
            json.dumps({"jsonrpc": "2.0", "method": "shutdown", "id": "bye"}),
        ],
    )
    with (
        patch("sys.stdin", StringIO(stdin + "\n")),
        patch("sys.stdout", new_callable=StringIO) as out,
    ):
        run_jsonrpc_server(service_for_store(store))
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert lines[0]["result"]["schema"] == "wip.capabilities/0.1"
    assert lines[1]["result"]["goal"]["goal_id"]
    assert lines[2]["result"] is None


def test_protocol_error_includes_wip_error(tmp_path: Path) -> None:
    server = JsonRpcServer(service_for_store(tmp_path / "store"))
    initialize_rpc(server)
    response = rpc_request(server, "goal.status", {"goal_id": "goal_missing"})
    assert response["error"]["code"] == -32000
    data = response["error"]["data"]
    assert data["schema"] == "wip.error/0.1"
