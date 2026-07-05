"""wayfinder-exec-temporal CLI entry point (Phase 7b §9.6)."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from pathlib import Path

from wayfinder.cli.responses import map_exception, success_response, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.wayfinder_client import parse_wayfinder_command
from wayfinder.exec_temporal.runner import ExecutorRunRequest, run_executor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayfinder-exec-temporal",
        description="Durable executor backed by Temporal workflows (§9.6)",
    )
    parser.add_argument("--store", help="Override wayfinder store root")
    parser.add_argument("--wayfinder", help="Alternate wayfinder command (shell-quoted)")
    parser.add_argument("--brain-playbook", help="JSON playbook path passed through to wayfinder")
    parser.add_argument(
        "--executor-id",
        default=os.environ.get("WAYFINDER_EXECUTOR_ID", "wayfinder-exec-temporal-local"),
    )
    parser.add_argument("--policy", type=Path, help="Policy file override (JSON or YAML)")
    parser.add_argument(
        "--temporal-address",
        default=os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        help="Temporal frontend address",
    )
    parser.add_argument(
        "--task-queue",
        default=os.environ.get("WAYFINDER_TEMPORAL_TASK_QUEUE", "wayfinder-exec"),
    )
    parser.add_argument("--format", default="json", choices=["json"])
    parser.add_argument("--request-id")

    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the durable executor loop until stopped")
    run.add_argument("--goal-id")
    run.add_argument("--goal-file", type=Path)

    dry_run = subparsers.add_parser("dry-run", help="Preview one recommendation without executing")
    dry_run.add_argument("--goal-id")
    dry_run.add_argument("--goal-file", type=Path)
    return parser


def _resolve_goal_id(args: argparse.Namespace) -> str:
    if args.goal_id:
        return str(args.goal_id)
    if args.goal_file:
        return str(args.goal_file.read_text(encoding="utf-8").strip())
    msg = "one of --goal-id or --goal-file is required"
    raise InvalidInputError(msg)


def _use_stub_mode() -> bool:
    return os.environ.get("WAYFINDER_TEMPORAL_STUB", "").lower() in {"1", "true", "yes"}


def _run_request(args: argparse.Namespace) -> dict[str, object]:
    goal_id = _resolve_goal_id(args)
    wayfinder_command = (
        parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
    )
    request = ExecutorRunRequest(
        goal_id=goal_id,
        store=args.store,
        executor_id=args.executor_id,
        wayfinder_command=wayfinder_command,
        brain_playbook=args.brain_playbook,
        policy_path=str(args.policy) if args.policy is not None else None,
        dry_run=args.command == "dry-run",
    )
    if _use_stub_mode():
        outcome = run_executor(request)
        return {
            "goal_id": goal_id,
            "stopped_reason": outcome.stopped_reason,
            "status": outcome.status,
            "recommendation": outcome.recommendation,
            "extensions": {"temporal": True, "stub": True},
        }

    from wayfinder.exec_temporal.workflow import run_temporal_workflow

    workflow_id = f"wayfinder-exec-{goal_id}-{secrets.token_hex(4)}"
    payload = asyncio.run(
        run_temporal_workflow(
            request,
            task_queue=args.task_queue,
            temporal_address=args.temporal_address,
            workflow_id=workflow_id,
        ),
    )
    return {
        "goal_id": goal_id,
        "stopped_reason": payload["stopped_reason"],
        "status": payload["status"],
        "recommendation": payload.get("recommendation"),
        "extensions": {"temporal": True, "workflow_id": workflow_id},
    }


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = getattr(args, "request_id", None)
    command_name = f"wayfinder-exec-temporal.{args.command}"

    try:
        result_payload = _run_request(args)
        result = {
            "schema": "wip.executor_result/0.1",
            "protocol_version": "0.1",
            **result_payload,
        }
        write_json(success_response(command_name, result, request_id=request_id))
        raise SystemExit(0)
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc, request_id=request_id)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
