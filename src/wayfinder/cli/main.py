"""Wayfinder CLI entry point (Phase 3)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from wayfinder.brains.base import Brain
from wayfinder.brains.llm import LLMBrain
from wayfinder.brains.scripted import ScriptedBrain
from wayfinder.cli.doctor import run_doctor
from wayfinder.cli.jsonrpc import run_jsonrpc_server
from wayfinder.cli.responses import map_exception, success_response, write_json
from wayfinder.cli.service import WayfinderService
from wayfinder.core.errors import InvalidInputError
from wayfinder.core.hash_chain import CorruptEventLogError


def _build_parser(*, prog: str = "wayfinder") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument(
        "--store",
        help="Override wayfinder store root (or set WAYFINDER_STORE)",
    )
    parser.add_argument(
        "--brain",
        choices=["scripted", "llm"],
        default=os.environ.get("WAYFINDER_BRAIN", "scripted"),
        help="Recommendation brain to use",
    )
    parser.add_argument(
        "--brain-playbook",
        help="JSON playbook path for the scripted brain",
    )
    parser.add_argument(
        "--jsonrpc-stdio",
        action="store_true",
        help="Run as a JSON-RPC 2.0 server on stdin/stdout (§1.5)",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    caps = subparsers.add_parser("capabilities", help="Show wayfinder capabilities")
    caps.add_argument("--format", default="json", choices=["json"])
    caps.add_argument("--request-id")

    goal_create = subparsers.add_parser("goal", help="Goal operations")
    goal_sub = goal_create.add_subparsers(dest="goal_command", required=True)
    create = goal_sub.add_parser("create", help="Create a goal from stdin JSON")
    create.add_argument("--format", default="json", choices=["json"])
    create.add_argument("--request-id")

    status = subparsers.add_parser("status", help="Read reduced goal status")
    status.add_argument("--goal-id", required=True)
    status.add_argument("--format", default="json", choices=["json"])
    status.add_argument("--request-id")

    nxt = subparsers.add_parser("next", help="Preview or issue the next recommendation")
    nxt.add_argument("--goal-id", required=True)
    nxt.add_argument("--mode", required=True, choices=["preview", "issue"])
    nxt.add_argument("--supersede", action="store_true")
    nxt.add_argument(
        "--explain",
        default="none",
        choices=["none", "summary", "structured", "debug"],
    )
    nxt.add_argument("--format", default="json", choices=["json"])
    nxt.add_argument("--request-id")

    update = subparsers.add_parser("update", help="Submit an update from stdin JSON")
    update.add_argument("--goal-id", required=True)
    update.add_argument("--format", default="json", choices=["json"])
    update.add_argument("--request-id")

    history = subparsers.add_parser("history", help="Stream goal events as JSONL")
    history.add_argument("--goal-id", required=True)
    history.add_argument("--since-seq", type=int, required=True)
    history.add_argument("--limit", type=int)
    history.add_argument("--format", default="jsonl", choices=["jsonl"])

    explain = subparsers.add_parser("explain", help="Explain an issued recommendation")
    explain.add_argument("--goal-id", required=True)
    explain.add_argument("--recommendation-id", required=True)
    explain.add_argument("--format", default="json", choices=["json"])
    explain.add_argument("--request-id")

    verify = subparsers.add_parser("verify", help="Verify event log and artifacts")
    verify.add_argument("--goal-id", required=True)
    verify.add_argument("--format", default="json", choices=["json"])
    verify.add_argument("--request-id")

    doctor = subparsers.add_parser("doctor", help="Check dependencies and credentials")
    doctor.add_argument("--format", default="json", choices=["json"])
    doctor.add_argument("--request-id")

    return parser


def _load_brain(args: argparse.Namespace) -> Brain:
    if args.brain == "scripted":
        playbook_path = args.brain_playbook or os.environ.get("WAYFINDER_BRAIN_PLAYBOOK")
        if playbook_path:
            return ScriptedBrain.from_path(Path(playbook_path))
        return ScriptedBrain.default()
    if args.brain == "llm":
        return LLMBrain.from_config()
    msg = f"unsupported brain: {args.brain}"
    raise InvalidInputError(msg)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        msg = "expected JSON document on stdin"
        raise InvalidInputError(msg)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        msg = "stdin JSON must be an object"
        raise InvalidInputError(msg)
    return parsed


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "goal":
        return "wayfinder.goal.create"
    return f"wayfinder.{args.command}"


def _dispatch(service: WayfinderService, args: argparse.Namespace) -> dict[str, Any]:
    store = args.store
    if args.command == "capabilities":
        return service.capabilities()
    if args.command == "goal" and args.goal_command == "create":
        return service.goal_create(_read_stdin_json(), store=store)
    if args.command == "status":
        return service.status(args.goal_id, store=store)
    if args.command == "next":
        return service.next(
            args.goal_id,
            mode=args.mode,
            supersede=args.supersede,
            explain_mode=args.explain,
            store=store,
        )
    if args.command == "update":
        return service.update(args.goal_id, _read_stdin_json(), store=store)
    if args.command == "explain":
        return service.explain(args.goal_id, args.recommendation_id, store=store)
    if args.command == "verify":
        return service.verify(args.goal_id, store=store)
    if args.command == "doctor":
        return run_doctor()
    msg = f"unsupported command: {args.command}"
    raise InvalidInputError(msg)


def run_cli(
    argv: list[str] | None = None,
    *,
    brain: Brain | None = None,
    prog: str = "wayfinder",
) -> None:
    """Parse CLI arguments and run one wayfinder command."""
    parser = _build_parser(prog=prog)
    args = parser.parse_args(argv)
    request_id = getattr(args, "request_id", None)
    resolved_brain = brain if brain is not None else _load_brain(args)

    if args.jsonrpc_stdio:
        try:
            service = WayfinderService(brain=resolved_brain, store_root=None)
            run_jsonrpc_server(service)
            raise SystemExit(0)
        except SystemExit:
            raise
        except BaseException as exc:
            payload, code = map_exception(exc, request_id=request_id)
            write_json(payload)
            raise SystemExit(code) from exc

    if args.command is None:
        parser.error("the following arguments are required: command")

    if args.command == "history":
        try:
            service = WayfinderService(brain=resolved_brain, store_root=None)
            streamed = False
            for line in service.history_iter(
                args.goal_id,
                since_seq=args.since_seq,
                limit=args.limit,
                store=args.store,
            ):
                streamed = True
                sys.stdout.write(line)
                sys.stdout.flush()
            raise SystemExit(0)
        except SystemExit:
            raise
        except BaseException as exc:
            payload, code = map_exception(exc, request_id=request_id)
            if streamed or not isinstance(exc, CorruptEventLogError):
                write_json(payload)
            raise SystemExit(code) from exc

    try:
        service = WayfinderService(brain=resolved_brain, store_root=None)
        result = _dispatch(service, args)
        write_json(success_response(_command_name(args), result, request_id=request_id))
        raise SystemExit(0)
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc, request_id=request_id)
        write_json(payload)
        raise SystemExit(code) from exc


def main(argv: list[str] | None = None) -> None:
    run_cli(argv)
