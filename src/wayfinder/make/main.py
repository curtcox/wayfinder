"""wayfinder-make CLI entry point (Phase 7a)."""

from __future__ import annotations

import sys

from wayfinder.brains.make import MakeBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a Make-backed brain for one target."""
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if not args or args[0].startswith("-"):
            msg = "usage: wayfinder-make <target> [wayfinder args...]"
            raise InvalidInputError(msg)
        target = args[0]
        remaining = args[1:]
        brain = MakeBrain(target)
        run_cli(remaining, brain=brain, prog=f"wayfinder-make {target}")
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
