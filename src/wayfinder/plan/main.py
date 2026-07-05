"""wayfinder-plan CLI entry point (Phase 7a)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wayfinder.brains.plan import PlanBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config


def _split_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split wayfinder-plan flags from wayfinder subcommand args."""
    plan_args: list[str] = []
    wayfinder_args: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--domain":  # nosec B105
            if index + 1 >= len(argv):
                msg = "--domain requires a path argument"
                raise InvalidInputError(msg)
            plan_args.extend([token, argv[index + 1]])
            index += 2
            continue
        if token in {"-h", "--help"}:
            plan_args.append("--help")
            index += 1
            continue
        wayfinder_args = argv[index:]
        break
    if not plan_args or "--domain" not in plan_args:
        msg = "usage: wayfinder-plan --domain <file.pddl> [wayfinder args...]"
        raise InvalidInputError(msg)
    return plan_args, wayfinder_args


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a PDDL planner-backed brain."""
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        if not raw or raw[0] in {"-h", "--help"}:
            msg = "usage: wayfinder-plan --domain <file.pddl> [wayfinder args...]"
            raise InvalidInputError(msg)
        plan_args, wayfinder_args = _split_args(raw)
        parser = argparse.ArgumentParser(prog="wayfinder-plan")
        parser.add_argument("--domain", required=True, help="Path to a PDDL domain file")
        parsed = parser.parse_args(plan_args)
        domain_path = Path(parsed.domain)
        llm_client: ChatClient | None = None
        try:
            llm_client = ChatClient(load_llm_config())
        except Exception:
            llm_client = None
        brain = PlanBrain(domain_path, llm_client=llm_client)
        run_cli(
            wayfinder_args,
            brain=brain,
            prog=f"wayfinder-plan --domain {domain_path.name}",
        )
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
