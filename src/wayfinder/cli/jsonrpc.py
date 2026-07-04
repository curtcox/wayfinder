"""JSON-RPC 2.0 stdio server (§1.5)."""

from __future__ import annotations

import json
import sys
from typing import Any

from wayfinder.cli.responses import map_exception
from wayfinder.cli.service import WayfinderService
from wayfinder.core.errors import InvalidInputError

PROTOCOL_ERROR = -32000


class JsonRpcServer:
    """Long-running JSON-RPC server over stdin/stdout."""

    def __init__(self, service: WayfinderService) -> None:
        self.service = service
        self._initialized = False
        self._running = True

    def run(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None,
                    },
                )
                continue
            if isinstance(request, list):
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32600, "message": "Invalid Request"},
                        "id": None,
                    },
                )
                continue
            if not isinstance(request, dict):
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32600, "message": "Invalid Request"},
                        "id": request.get("id") if isinstance(request, dict) else None,
                    },
                )
                continue
            response = self.handle_request(request)
            if response is not None:
                self._write(response)
            if not self._running:
                break

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        if request.get("jsonrpc") != "2.0":
            return self._error_response(-32600, "Invalid Request", request_id)
        method = request.get("method")
        if not isinstance(method, str):
            return self._error_response(-32600, "Invalid Request", request_id)
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error_response(-32602, "Invalid params", request_id)

        try:
            if method == "initialize":
                result: Any = self._handle_initialize(params)
            elif method == "shutdown":
                self._handle_shutdown()
                return {"jsonrpc": "2.0", "result": None, "id": request_id}
            elif not self._initialized:
                msg = "initialize must be the first call on a connection"
                raise InvalidInputError(msg)
            elif method == "wayfinder.capabilities":
                result = self.service.capabilities()
            elif method == "goal.create":
                result = self.service.goal_create(params)
            elif method == "goal.status":
                result = self._require_params(params, ("goal_id",))
                result = self.service.status(str(params["goal_id"]))
            elif method == "wayfinder.next":
                result = self._handle_next(params)
            elif method == "wayfinder.update":
                goal_id = str(params["goal_id"])
                result = self.service.update(goal_id, params)
            elif method == "goal.history":
                result = self._handle_history(params)
            elif method == "wayfinder.explain":
                result = self._require_params(params, ("goal_id", "recommendation_id"))
                result = self.service.explain(
                    str(params["goal_id"]),
                    str(params["recommendation_id"]),
                )
            else:
                return self._error_response(-32601, "Method not found", request_id)
        except BaseException as exc:
            return self._protocol_error(exc, request_id)

        return {"jsonrpc": "2.0", "result": result, "id": request_id}

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._initialized:
            msg = "initialize already called on this connection"
            raise InvalidInputError(msg)
        version = params.get("protocol_version")
        if version != "0.1":
            msg = "unsupported protocol_version"
            raise InvalidInputError(msg)
        client = params.get("client")
        if not isinstance(client, dict):
            msg = "initialize requires client object"
            raise InvalidInputError(msg)
        if not isinstance(client.get("name"), str) or not isinstance(client.get("version"), str):
            msg = "client name and version are required strings"
            raise InvalidInputError(msg)
        self._initialized = True
        return self.service.capabilities()

    def _handle_shutdown(self) -> None:
        self._running = False

    def _handle_next(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_params(params, ("goal_id", "mode"))
        return self.service.next(
            str(params["goal_id"]),
            mode=str(params["mode"]),
            supersede=bool(params.get("supersede", False)),
            explain_mode=str(params.get("explain", "none")),
        )

    def _handle_history(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_params(params, ("goal_id", "since_seq"))
        limit = params.get("limit")
        parsed_limit = int(limit) if limit is not None else None
        return self.service.history_page(
            str(params["goal_id"]),
            since_seq=int(params["since_seq"]),
            limit=parsed_limit,
        )

    @staticmethod
    def _require_params(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        for key in keys:
            if key not in params:
                msg = f"missing required param: {key}"
                raise InvalidInputError(msg)
        return params

    def _protocol_error(self, exc: BaseException, request_id: Any) -> dict[str, Any]:
        payload, _code = map_exception(exc, request_id=str(request_id) if request_id else None)
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": PROTOCOL_ERROR,
                "message": payload["error"]["message"],
                "data": payload,
            },
            "id": request_id,
        }

    @staticmethod
    def _error_response(code: int, message: str, request_id: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}

    @staticmethod
    def _write(response: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
        sys.stdout.flush()


def run_jsonrpc_server(service: WayfinderService) -> None:
    """Run the JSON-RPC server until shutdown or EOF."""
    JsonRpcServer(service).run()
