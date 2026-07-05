"""wayfinder-wrap CLI entry point (Phase 5)."""

from __future__ import annotations

import sys

from wayfinder.brains.wrap import WrapBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a tool-specialized wrap brain."""
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if not args or args[0].startswith("-"):
            msg = "usage: wayfinder-wrap <tool> [wayfinder args...]"
            raise InvalidInputError(msg)
        tool = args[0]
        remaining = args[1:]
        config = load_llm_config()
        brain = WrapBrain(tool, ChatClient(config))
        run_cli(remaining, brain=brain, prog=f"wayfinder-wrap {tool}")
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
