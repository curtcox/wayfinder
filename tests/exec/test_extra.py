"""Additional executor coverage tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.exec.durability import DurabilityStore, PendingAction
from wayfinder.exec.policy import check_preconditions, load_policy
from wayfinder.exec.shell_exec import (
    CommandResult,
    build_action_result,
    execute_shell_action,
    redact_text,
)
from wayfinder.exec.wayfinder_client import parse_wayfinder_command

from tests.conformance.helpers import create_goal_via_cli, run_cli


def test_load_policy_from_json_file(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps({"shell": {"denied_argv0": ["wget"]}}),
        encoding="utf-8",
    )
    loaded = load_policy(policy_path)
    assert "wget" in loaded["shell"]["denied_argv0"]
    assert "rm" in loaded["shell"]["denied_argv0"]


def test_check_preconditions_path_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing = tmp_path / "workspace" / "missing.txt"
    decision = check_preconditions(
        [{"kind": "path_exists", "path": str(missing)}],
        workspace_uri=f"file:{workspace}",
        events=[],
        recommendation_id="rec_1",
    )
    assert decision.denied is True


def test_check_preconditions_command_available(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = check_preconditions(
        [{"kind": "command_available", "command": "true"}],
        workspace_uri=f"file:{workspace}",
        events=[],
        recommendation_id="rec_1",
    )
    assert decision.denied is False


def test_check_preconditions_unknown_kind(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = check_preconditions(
        [{"kind": "unsupported_kind"}],
        workspace_uri=f"file:{workspace}",
        events=[],
        recommendation_id="rec_1",
    )
    assert decision.reason_code == "unsupported_precondition"


def test_redact_text_masks_secrets() -> None:
    text = "api_key=supersecret token: abc123"
    assert "[REDACTED]" in redact_text(text)


def test_build_action_result_spills_large_stdout(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    result = build_action_result(
        CommandResult(
            exit_code=0,
            signal=None,
            timed_out=False,
            stdout=b"x" * 10000,
            stderr=b"",
            started_at="2026-07-04T18:00:00Z",
            ended_at="2026-07-04T18:01:00Z",
        ),
        action={"kind": "shell", "shell": {"expected_exit_codes": [0]}},
        artifact_store=store,
        inline_limit=100,
    )
    assert "artifacts" in result
    assert "stdout_artifact" in result["output"]


def test_execute_shell_action_runs_true(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = execute_shell_action(
        {
            "kind": "shell",
            "shell": {
                "argv": ["true"],
                "cwd": f"file:{workspace}",
                "env": {"mode": "minimal", "set": {}},
                "timeout_seconds": 30,
            },
        },
        workspace_uri=f"file:{workspace}",
    )
    assert result.exit_code == 0
    assert result.timed_out is False


def test_parse_wayfinder_command_splits_shell_words() -> None:
    assert parse_wayfinder_command("wayfinder --store /data/store") == [
        "wayfinder",
        "--store",
        "/data/store",
    ]


def test_resume_started_without_result_submits_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    playbook = Path(__file__).parent / "fixtures" / "true_playbook.json"
    created = create_goal_via_cli(store, workspace)
    goal_id = created["goal"]["goal_id"]

    issued = run_cli(
        [
            "--store",
            str(store),
            "--brain-playbook",
            str(playbook),
            "next",
            "--goal-id",
            goal_id,
            "--mode=issue",
        ],
    )
    recommendation = json.loads(issued.stdout)["result"]
    history = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    issued_event = json.loads(history.stdout.strip().splitlines()[-1])

    durability = DurabilityStore(store, executor_id="wayfinder-exec-local")
    durability.save(
        PendingAction(
            goal_id=goal_id,
            recommendation_id=recommendation["recommendation_id"],
            action_id=recommendation["action"]["action_id"],
            issued_event_seq=int(issued_event["seq"]),
            issued_event_hash=str(issued_event["event_hash"]),
            accept_update_id="upd_accept_only",
            start_update_id="upd_start_only",
            result_update_id="upd_result_only",
            stage="started",
        ),
    )

    cmd = [
        sys.executable,
        "-m",
        "wayfinder.exec",
        "--store",
        str(store),
        "--brain-playbook",
        str(playbook),
        "run",
        "--goal-id",
        goal_id,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    history_after = run_cli(
        ["--store", str(store), "history", "--goal-id", goal_id, "--since-seq", "0"],
    )
    event_types = [
        json.loads(line)["type"] for line in history_after.stdout.splitlines() if line.strip()
    ]
    assert "action.blocked" in event_types
