"""wayfinder-ask CLI entry point (Phase 6)."""

from __future__ import annotations

import argparse
import sys
from typing import IO

from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.wayfinder_client import WayfinderClient, parse_wayfinder_command
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config
from wayfinder.prose.ask import answer_for_goal, answer_for_store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wayfinder-ask")
    parser.add_argument(
        "question",
        nargs="+",
        help="Question in plain language",
    )
    parser.add_argument("--goal-id", help="Goal to query (omit for store-wide mode)")
    parser.add_argument(
        "--recommendation",
        help="Review a specific recommendation before approval (§8.4)",
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
        "--format",
        default="text",
        choices=["text", "json"],
        help="Output format",
    )
    parser.add_argument("--request-id")
    return parser


def run_ask(
    question: str,
    *,
    goal_id: str | None = None,
    recommendation_id: str | None = None,
    store: str | None = None,
    wayfinder_command: list[str] | None = None,
    brain_playbook: str | None = None,
    output_format: str = "text",
    client: ChatClient | None = None,
    output_stream: IO[str] | None = None,
) -> dict[str, object]:
    """Synthesize a read-only answer from goal or store state."""
    llm_client = client or ChatClient(load_llm_config())
    wf = WayfinderClient(
        command=wayfinder_command,
        store=store,
        brain_playbook=brain_playbook,
    )
    if goal_id is not None:
        answer = answer_for_goal(
            wf,
            llm_client,
            goal_id,
            question,
            recommendation_id=recommendation_id,
        )
    else:
        if recommendation_id is not None:
            msg = "--recommendation requires --goal-id"
            raise InvalidInputError(msg)
        answer = answer_for_store(llm_client, question, store=store)

    if output_format == "text":
        stream = output_stream or sys.stdout
        stream.write(answer + "\n")
        stream.flush()

    return {
        "goal_id": goal_id,
        "recommendation_id": recommendation_id,
        "question": question,
        "answer": answer,
    }


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = args.request_id
    question = " ".join(args.question).strip()
    if not question:
        msg = "question must not be empty"
        raise InvalidInputError(msg)

    try:
        wayfinder_command = (
            parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
        )
        result = run_ask(
            question,
            goal_id=args.goal_id,
            recommendation_id=args.recommendation,
            store=args.store,
            wayfinder_command=wayfinder_command,
            brain_playbook=args.brain_playbook,
            output_format=args.format,
        )
        if args.format == "json":
            write_json(
                {
                    "schema": "wip.response/0.1",
                    "protocol_version": "0.1",
                    "command": "wayfinder-ask",
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
