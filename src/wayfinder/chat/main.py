"""wayfinder-chat interactive CLI (Phase 6)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import IO

from wayfinder.ask.main import run_ask
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.exec.wayfinder_client import WayfinderClient, parse_wayfinder_command
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config
from wayfinder.prose.context import gather_goal_context
from wayfinder.tell.main import run_tell


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wayfinder-chat")
    parser.add_argument("--goal-id", required=True, help="Goal for this session")
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


def _status_banner(context_status: dict[str, object]) -> str:
    goal_status = str(context_status.get("goal_status", "unknown"))
    open_id = context_status.get("open_recommendation_id")
    if open_id:
        return f"chat: {context_status.get('goal_id', 'goal')} is {goal_status} — {open_id} open"
    return f"chat: {context_status.get('goal_id', 'goal')} is {goal_status}"


def _looks_like_question(text: str) -> bool:
    stripped = text.strip().lower()
    if stripped.endswith("?"):
        return True
    prefixes = (
        "why ",
        "what ",
        "when ",
        "where ",
        "who ",
        "how ",
        "did ",
        "has ",
        "have ",
        "show ",
        "summarize ",
        "explain ",
    )
    return stripped.startswith(prefixes)


def run_chat_turn(
    user_input: str,
    *,
    goal_id: str,
    store: str | None = None,
    wayfinder_command: list[str] | None = None,
    brain_playbook: str | None = None,
    client: ChatClient | None = None,
    output_stream: IO[str] | None = None,
) -> dict[str, object]:
    """Handle one chat turn as either a read or an update."""
    llm_client = client or ChatClient(load_llm_config())
    stream = output_stream or sys.stdout
    if _looks_like_question(user_input):
        result = run_ask(
            user_input,
            goal_id=goal_id,
            store=store,
            wayfinder_command=wayfinder_command,
            brain_playbook=brain_playbook,
            client=llm_client,
            output_stream=stream,
        )
        return {"kind": "ask", **result}
    result = run_tell(
        user_input,
        goal_id,
        store=store,
        wayfinder_command=wayfinder_command,
        brain_playbook=brain_playbook,
        client=llm_client,
        output_stream=stream,
    )
    return {"kind": "tell", **result}


def run_chat(
    *,
    goal_id: str,
    store: str | None = None,
    wayfinder_command: list[str] | None = None,
    brain_playbook: str | None = None,
    output_format: str = "text",
    client: ChatClient | None = None,
    input_stream: IO[str] | None = None,
    output_stream: IO[str] | None = None,
    read_line: Callable[[], str | None] | None = None,
) -> list[dict[str, object]]:
    """Run an interactive chat session until EOF."""
    llm_client = client or ChatClient(load_llm_config())
    wf = WayfinderClient(
        command=wayfinder_command,
        store=store,
        brain_playbook=brain_playbook,
    )
    context = gather_goal_context(wf, goal_id)
    stream = output_stream or sys.stdout
    turns: list[dict[str, object]] = []

    if output_format == "text":
        stream.write(_status_banner({**context.status, "goal_id": goal_id}) + "\n")
        stream.flush()

    reader = read_line
    if reader is None:
        reader = (input_stream or sys.stdin).readline

    while True:
        line = reader()
        if line is None or not line.strip():
            break
        user_input = line.strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        turn = run_chat_turn(
            user_input,
            goal_id=goal_id,
            store=store,
            wayfinder_command=wayfinder_command,
            brain_playbook=brain_playbook,
            client=llm_client,
            output_stream=stream if output_format == "text" else None,
        )
        turns.append(turn)

    return turns


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = args.request_id

    try:
        wayfinder_command = (
            parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
        )
        turns = run_chat(
            goal_id=args.goal_id,
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
                    "command": "wayfinder-chat",
                    "result": {"goal_id": args.goal_id, "turns": turns},
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
