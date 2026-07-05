"""wayfinder-do CLI entry point (Phase 6)."""

from __future__ import annotations

import argparse
import sys
from typing import IO

from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.loop import ExecutorConfig, ExecutorLoop
from wayfinder.exec.wayfinder_client import WayfinderClient, parse_wayfinder_command
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config
from wayfinder.prose.goal import compose_goal_create, generate_goal_create_draft
from wayfinder.prose.narrate import (
    NarratingReporter,
    format_goal_created,
    format_goal_finished,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wayfinder-do")
    parser.add_argument(
        "intent",
        nargs="+",
        help="Goal intent in plain language",
    )
    parser.add_argument(
        "--store",
        help="Override wayfinder store root (or set WAYFINDER_STORE)",
    )
    parser.add_argument(
        "--wayfinder",
        help="Alternate wayfinder command (shell-quoted)",
    )
    parser.add_argument(
        "--brain-playbook",
        help="JSON playbook path passed through to wayfinder",
    )
    parser.add_argument(
        "--executor-id",
        default="wayfinder-do",
        help="Executor actor id recorded in updates",
    )
    parser.add_argument(
        "--format",
        default="text",
        choices=["text", "json"],
        help="Output format (text narrates; json returns envelopes)",
    )
    parser.add_argument("--request-id")
    return parser


def run_do(
    intent: str,
    *,
    store: str | None = None,
    wayfinder_command: list[str] | None = None,
    brain_playbook: str | None = None,
    executor_id: str = "wayfinder-do",
    output_format: str = "text",
    client: ChatClient | None = None,
    output_stream: IO[str] | None = None,
) -> dict[str, object]:
    """Create a goal from prose and drive the executor loop."""
    llm_client = client or ChatClient(load_llm_config())
    draft = generate_goal_create_draft(llm_client, intent)
    goal_create = compose_goal_create(draft)
    wf = WayfinderClient(
        command=wayfinder_command,
        store=store,
        brain_playbook=brain_playbook,
    )
    created = wf.goal_create(goal_create)
    goal = created.get("goal", {})
    if not isinstance(goal, dict):
        msg = "goal create response missing goal object"
        raise InvalidInputError(msg)
    goal_id = str(goal.get("goal_id", ""))
    if not goal_id:
        msg = "goal create response missing goal_id"
        raise InvalidInputError(msg)

    if output_format == "text":
        stream = output_stream or sys.stdout
        stream.write(format_goal_created(goal) + "\n")
        stream.flush()

    reporter = (
        NarratingReporter(stream=output_stream or sys.stdout) if output_format == "text" else None
    )
    config = ExecutorConfig(
        goal_id=goal_id,
        store=store,
        executor_id=executor_id,
        wayfinder_command=wayfinder_command,
        brain_playbook=brain_playbook,
        policy_path=None,
        dry_run=False,
        reporter=reporter,
    )
    outcome = ExecutorLoop(config).run()

    if output_format == "text":
        stream = output_stream or sys.stdout
        stream.write(format_goal_finished(goal_id, outcome.status) + "\n")
        stream.flush()

    return {
        "goal_id": goal_id,
        "goal": goal,
        "goal_create": goal_create,
        "stopped_reason": outcome.stopped_reason,
        "status": outcome.status,
        "recommendation": outcome.recommendation,
    }


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = args.request_id
    intent = " ".join(args.intent).strip()
    if not intent:
        msg = "intent must not be empty"
        raise InvalidInputError(msg)

    try:
        wayfinder_command = (
            parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
        )
        result = run_do(
            intent,
            store=args.store,
            wayfinder_command=wayfinder_command,
            brain_playbook=args.brain_playbook,
            executor_id=args.executor_id,
            output_format=args.format,
        )
        if args.format == "json":
            write_json(
                {
                    "schema": "wip.response/0.1",
                    "protocol_version": "0.1",
                    "command": "wayfinder-do",
                    "result": result,
                    **({"request_id": request_id} if request_id else {}),
                },
            )
        raise SystemExit(0)
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc, request_id=request_id)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
