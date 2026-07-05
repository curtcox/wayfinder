"""wayfinder-tell CLI entry point (Phase 6)."""

from __future__ import annotations

import argparse
import sys
from typing import IO

from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.wayfinder_client import WayfinderClient, parse_wayfinder_command
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config
from wayfinder.prose.context import gather_goal_context
from wayfinder.prose.update import (
    compose_update,
    format_update_receipt,
    generate_update_draft,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wayfinder-tell")
    parser.add_argument(
        "prose",
        nargs="+",
        help="Update intent in plain language",
    )
    parser.add_argument("--goal-id", required=True, help="Goal to update")
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
        help="Output format (text prints receipt; json returns envelopes)",
    )
    parser.add_argument("--request-id")
    return parser


def run_tell(
    prose: str,
    goal_id: str,
    *,
    store: str | None = None,
    wayfinder_command: list[str] | None = None,
    brain_playbook: str | None = None,
    output_format: str = "text",
    client: ChatClient | None = None,
    output_stream: IO[str] | None = None,
) -> dict[str, object]:
    """Classify prose into an update and submit it."""
    llm_client = client or ChatClient(load_llm_config())
    wf = WayfinderClient(
        command=wayfinder_command,
        store=store,
        brain_playbook=brain_playbook,
    )
    context = gather_goal_context(wf, goal_id)
    draft = generate_update_draft(llm_client, prose, context)
    update = compose_update(draft, context)
    result = wf.update(goal_id, update)
    receipt = format_update_receipt(update, result, context=context)

    if output_format == "text":
        stream = output_stream or sys.stdout
        stream.write(receipt + "\n")
        stream.flush()

    return {
        "goal_id": goal_id,
        "update": update,
        "result": result,
        "receipt": receipt,
    }


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    request_id = args.request_id
    prose = " ".join(args.prose).strip()
    if not prose:
        msg = "prose must not be empty"
        raise InvalidInputError(msg)

    try:
        wayfinder_command = (
            parse_wayfinder_command(args.wayfinder) if args.wayfinder is not None else None
        )
        result = run_tell(
            prose,
            args.goal_id,
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
                    "command": "wayfinder-tell",
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
