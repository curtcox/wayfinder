#!/usr/bin/env python3
"""Run a subprocess with a local OpenAI-compatible LLM stub."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _StubHandler(BaseHTTPRequestHandler):
    queue: list[str] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        payload = self.queue.pop(0)
        body = json.dumps({"choices": [{"message": {"content": payload}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--responses",
        required=True,
        type=Path,
        help="JSON file with a list of assistant response strings",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run")
    args = parser.parse_args()
    if not args.command:
        print("run_with_llm_stub: missing command", file=sys.stderr)
        return 2
    if args.command[0] == "--":
        args.command = args.command[1:]
    responses = json.loads(args.responses.read_text(encoding="utf-8"))
    if not isinstance(responses, list) or not responses:
        print("responses file must be a non-empty JSON array", file=sys.stderr)
        return 2

    _StubHandler.queue = [
        item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        for item in responses
    ]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    base_url = f"http://{host}:{port}/v1"

    env = os.environ.copy()
    env["WAYFINDER_LLM_BASE_URL"] = base_url
    env["WAYFINDER_LLM_API_KEY"] = "example-stub"
    env["WAYFINDER_LLM_MODEL"] = "stub-model"

    try:
        completed = subprocess.run(args.command, check=False, env=env)
        return int(completed.returncode)
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
