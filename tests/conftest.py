"""Shared test fixtures."""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class StubResponseQueue:
    items: list[str] = []


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        payload = StubResponseQueue.items.pop(0)
        body = json.dumps(
            {
                "choices": [
                    {"message": {"content": payload}},
                ],
            },
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def stub_server() -> Generator[str, None, None]:
    """Yield the base URL for a threaded OpenAI-compatible stub."""
    StubResponseQueue.items = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    yield f"http://{host}:{port}/v1"
    server.shutdown()
