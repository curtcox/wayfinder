"""wayfinder-tw CLI entry point (Phase 7a)."""

from __future__ import annotations

import sys

from wayfinder.brains.tw import TwBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a Taskwarrior-backed brain."""
    try:
        run_cli(list(sys.argv[1:] if argv is None else argv), brain=TwBrain(), prog="wayfinder-tw")
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
