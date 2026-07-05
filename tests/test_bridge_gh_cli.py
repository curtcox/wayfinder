"""wayfinder-bridge gh integration tests with a local GitHub API stub."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from tests.conformance.helpers import goal_create_payload, run_cli


class _GitHubStubState:
    issues: list[dict[str, Any]] = []
    comments: dict[int, list[dict[str, Any]]] = {}
    labels: dict[int, list[str]] = {}
    next_issue_number = 1
    next_comment_id = 1


class _GitHubStubHandler(BaseHTTPRequestHandler):
    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length))

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.endswith("/issues") and path.count("/") == 4:
            payload = self._read_json()
            issue_number = _GitHubStubState.next_issue_number
            _GitHubStubState.next_issue_number += 1
            issue = {
                "number": issue_number,
                "title": payload.get("title", ""),
                "body": payload.get("body", ""),
                "state": "open",
            }
            _GitHubStubState.issues.append(issue)
            _GitHubStubState.comments[issue_number] = []
            _GitHubStubState.labels[issue_number] = []
            self._send_json(201, issue)
            return
        if "/issues/" in path and path.endswith("/comments"):
            issue_number = int(path.split("/issues/")[1].split("/")[0])
            payload = self._read_json()
            comment_id = _GitHubStubState.next_comment_id
            _GitHubStubState.next_comment_id += 1
            comment = {
                "id": comment_id,
                "body": payload.get("body", ""),
                "user": {"login": "wayfinder-bridge[bot]"},
            }
            _GitHubStubState.comments.setdefault(issue_number, []).append(comment)
            self._send_json(201, comment)
            return
        if "/issues/" in path and path.endswith("/labels"):
            issue_number = int(path.split("/issues/")[1].split("/")[0])
            payload = self._read_json()
            labels = _GitHubStubState.labels.setdefault(issue_number, [])
            if isinstance(payload, list):
                labels.extend(str(item) for item in payload)
            self._send_json(200, [{"name": name} for name in labels])
            return
        self._send_json(404, {"message": "not found"})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if "/issues/" in path and path.endswith("/comments"):
            issue_number = int(path.split("/issues/")[1].split("/")[0])
            self._send_json(200, _GitHubStubState.comments.get(issue_number, []))
            return
        self._send_json(404, {"message": "not found"})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if "/issues/" in path:
            issue_number = int(path.split("/issues/")[1].split("/")[0])
            payload = self._read_json()
            for issue in _GitHubStubState.issues:
                if issue["number"] == issue_number:
                    issue.update(payload)
                    self._send_json(200, issue)
                    return
        self._send_json(404, {"message": "not found"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if "/issues/" in path and "/labels/" in path:
            issue_number = int(path.split("/issues/")[1].split("/")[0])
            label = unquote(path.split("/labels/")[1])
            labels = _GitHubStubState.labels.setdefault(issue_number, [])
            if label in labels:
                labels.remove(label)
            self.send_response(204)
            self.end_headers()
            return
        self._send_json(404, {"message": "not found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def github_stub() -> Generator[str, None, None]:
    _GitHubStubState.issues = []
    _GitHubStubState.comments = {}
    _GitHubStubState.labels = {}
    _GitHubStubState.next_issue_number = 1
    _GitHubStubState.next_comment_id = 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    yield f"http://{host}:{port}"
    server.shutdown()


def _run_bridge(args: list[str], *, api_base: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.bridge", *args]
    merged = {**os.environ, "GITHUB_API_BASE": api_base, "GITHUB_TOKEN": "test-token"}
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        env=merged,
    )


def _add_user_comment(issue_number: int, *, login: str, body: str) -> None:
    comment_id = _GitHubStubState.next_comment_id
    _GitHubStubState.next_comment_id += 1
    _GitHubStubState.comments.setdefault(issue_number, []).append(
        {
            "id": comment_id,
            "body": body,
            "user": {"login": login},
        },
    )


def test_bridge_gh_sync_creates_issue_and_comments(github_stub: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    payload = goal_create_payload(workspace, description="Rotate staging TLS certificates")
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]

    first = _run_bridge(
        ["gh", "sync", "--store", str(store), "--goal-id", goal_id, "--repo", "acme/ops"],
        api_base=github_stub,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    body = json.loads(first.stdout)
    assert body["result"]["issue_number"] == 1
    assert body["result"]["events_synced"] == 1
    assert len(_GitHubStubState.issues) == 1
    assert _GitHubStubState.issues[0]["title"] == "Rotate staging TLS certificates"

    run_cli(["--store", str(store), "next", "--goal-id", goal_id, "--mode=issue"])

    second = _run_bridge(
        ["gh", "sync", "--store", str(store), "--goal-id", goal_id, "--repo", "acme/ops"],
        api_base=github_stub,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    result = json.loads(second.stdout)["result"]
    assert result["events_synced"] == 1
    comments = _GitHubStubState.comments[1]
    assert any("make test" in comment["body"] for comment in comments)


def test_bridge_gh_maps_allowlisted_comment_to_approval(
    github_stub: str,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "jsmith": {
                    "actor_id": "jsmith",
                    "authority": "owner",
                    "authenticated": True,
                },
            },
        ),
        encoding="utf-8",
    )
    payload = goal_create_payload(workspace)
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    _run_bridge(
        [
            "gh",
            "sync",
            "--store",
            str(store),
            "--goal-id",
            goal_id,
            "--repo",
            "acme/ops",
            "--allowlist",
            str(allowlist),
        ],
        api_base=github_stub,
    )
    run_cli(["--store", str(store), "next", "--goal-id", goal_id, "--mode=issue"])
    _run_bridge(
        [
            "gh",
            "sync",
            "--store",
            str(store),
            "--goal-id",
            goal_id,
            "--repo",
            "acme/ops",
            "--allowlist",
            str(allowlist),
        ],
        api_base=github_stub,
    )
    _add_user_comment(
        1,
        login="jsmith",
        body="approved — renewal window confirmed with the platform team",
    )
    third = _run_bridge(
        [
            "gh",
            "sync",
            "--store",
            str(store),
            "--goal-id",
            goal_id,
            "--repo",
            "acme/ops",
            "--allowlist",
            str(allowlist),
        ],
        api_base=github_stub,
    )
    assert third.returncode == 0, third.stdout + third.stderr
    result = json.loads(third.stdout)["result"]
    assert result["updates_submitted"] == 1

    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [json.loads(line)["type"] for line in history.stdout.splitlines() if line.strip()]
    assert "approval.granted" in event_types


def test_bridge_gh_ignores_unlisted_commenters(github_stub: str, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({}), encoding="utf-8")
    payload = goal_create_payload(workspace)
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(payload),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    _run_bridge(
        [
            "gh",
            "sync",
            "--store",
            str(store),
            "--goal-id",
            goal_id,
            "--repo",
            "acme/ops",
            "--allowlist",
            str(allowlist),
        ],
        api_base=github_stub,
    )
    _add_user_comment(1, login="stranger", body="approved — not on the allowlist")
    synced = _run_bridge(
        [
            "gh",
            "sync",
            "--store",
            str(store),
            "--goal-id",
            goal_id,
            "--repo",
            "acme/ops",
            "--allowlist",
            str(allowlist),
        ],
        api_base=github_stub,
    )
    assert synced.returncode == 0
    assert json.loads(synced.stdout)["result"]["updates_submitted"] == 0
    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [json.loads(line)["type"] for line in history.stdout.splitlines() if line.strip()]
    assert "approval.granted" not in event_types
