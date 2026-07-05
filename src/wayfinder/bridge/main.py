"""wayfinder-bridge CLI entry point (Phase 7b §9.4)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from wayfinder.bridge.gh.sync import SyncConfig, sync_once
from wayfinder.cli.responses import map_exception, success_response, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.wayfinder_client import parse_wayfinder_command


def _gh_parent_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--store", help="Override wayfinder store root")
    parser.add_argument("--wayfinder", help="Alternate wayfinder command (shell-quoted)")
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--repo", required=True, help="GitHub repository owner/name")
    parser.add_argument("--allowlist", type=str, help="JSON file mapping GitHub logins to actors")
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayfinder-bridge",
        description="Mirror Wayfinder goals onto external trackers (§9.4)",
    )
    parser.add_argument("--format", default="json", choices=["json"])
    parser.add_argument("--request-id")

    subparsers = parser.add_subparsers(dest="backend", required=True)
    gh_parent = _gh_parent_parser()
    gh = subparsers.add_parser("gh", help="GitHub Issues bridge")
    gh_sub = gh.add_subparsers(dest="command", required=True)
    gh_sub.add_parser(
        "sync",
        parents=[gh_parent],
        help="Sync goal events and issue comments once",
    )
    run = gh_sub.add_parser(
        "run",
        parents=[gh_parent],
        help="Run sync in a loop until interrupted",
    )
    run.add_argument("--poll-interval", type=float, default=30.0)
    return parser


def _config_from_args(args: argparse.Namespace) -> SyncConfig:
    wayfinder_command = (
        parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
    )
    allowlist_path = Path(args.allowlist) if args.allowlist else None
    return SyncConfig(
        goal_id=str(args.goal_id),
        repo=str(args.repo),
        store=args.store,
        allowlist_path=allowlist_path,
        wayfinder_command=wayfinder_command,
    )


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = getattr(args, "request_id", None)
    command_name = f"wayfinder-bridge.{args.backend}.{args.command}"

    try:
        if args.backend != "gh":
            msg = f"unsupported bridge backend: {args.backend}"
            raise InvalidInputError(msg)
        config = _config_from_args(args)
        if args.command == "sync":
            outcome = sync_once(config)
        elif args.command == "run":
            while True:
                outcome = sync_once(config)
                time.sleep(max(args.poll_interval, 1.0))
        else:
            msg = f"unsupported gh command: {args.command}"
            raise InvalidInputError(msg)

        result = {
            "schema": "wip.bridge_result/0.1",
            "protocol_version": "0.1",
            "backend": "gh",
            "goal_id": config.goal_id,
            "repo": config.repo,
            "issue_number": outcome.issue_number,
            "events_synced": outcome.events_synced,
            "comments_processed": outcome.comments_processed,
            "updates_submitted": outcome.updates_submitted,
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
