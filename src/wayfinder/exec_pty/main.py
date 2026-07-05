"""wayfinder-exec-pty CLI entry point (Phase 7a §9.8 extension)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from wayfinder.cli.responses import map_exception, success_response, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.loop import ExecutorConfig
from wayfinder.exec.pty_loop import PtyExecutorLoop
from wayfinder.exec.secrets import default_secrets_path
from wayfinder.exec.wayfinder_client import parse_wayfinder_command


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayfinder-exec-pty",
        description=(
            "Executor with pty/pexpect support (§9.8 extension beyond strict v0.1 policy)"
        ),
    )
    parser.add_argument("--store", help="Override wayfinder store root")
    parser.add_argument("--wayfinder", help="Alternate wayfinder command (shell-quoted)")
    parser.add_argument("--brain-playbook", help="JSON playbook path passed through to wayfinder")
    parser.add_argument(
        "--executor-id",
        default=os.environ.get("WAYFINDER_EXECUTOR_ID", "wayfinder-exec-pty-local"),
    )
    parser.add_argument("--policy", type=Path, help="Policy file override (JSON or YAML)")
    parser.add_argument(
        "--secrets",
        type=Path,
        help="Secret store path (default: ~/.config/wayfinder/secrets.toml)",
    )
    parser.add_argument("--format", default="json", choices=["json"])
    parser.add_argument("--request-id")

    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the executor loop until stopped")
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


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = getattr(args, "request_id", None)
    command_name = f"wayfinder-exec-pty.{args.command}"

    try:
        goal_id = _resolve_goal_id(args)
        wayfinder_command = (
            parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
        )
        secrets_path = args.secrets if args.secrets is not None else default_secrets_path()
        config = ExecutorConfig(
            goal_id=goal_id,
            store=args.store,
            executor_id=args.executor_id,
            wayfinder_command=wayfinder_command,
            brain_playbook=args.brain_playbook,
            policy_path=args.policy,
            dry_run=args.command == "dry-run",
        )
        outcome = PtyExecutorLoop(config, secrets_path=secrets_path).run()
        result = {
            "schema": "wip.executor_result/0.1",
            "protocol_version": "0.1",
            "goal_id": goal_id,
            "stopped_reason": outcome.stopped_reason,
            "status": outcome.status,
            "extensions": {"pty": True},
        }
        if outcome.recommendation is not None:
            result["recommendation"] = outcome.recommendation
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
