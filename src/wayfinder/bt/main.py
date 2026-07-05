"""wayfinder-bt CLI entry point (Phase 7a)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wayfinder.brains.bt import BtBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.core.errors import InvalidInputError


def _split_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split wayfinder-bt flags from wayfinder subcommand args."""
    bt_args: list[str] = []
    wayfinder_args: list[str] = []
    parser = argparse.ArgumentParser(prog="wayfinder-bt", add_help=False)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--help", action="store_true")
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--tree":  # nosec B105
            if index + 1 >= len(argv):
                msg = "--tree requires a path argument"
                raise InvalidInputError(msg)
            bt_args.extend([token, argv[index + 1]])
            index += 2
            continue
        if token == "--help":  # nosec B105
            bt_args.append(token)
            index += 1
            continue
        wayfinder_args = argv[index:]
        break
    if not bt_args or "--tree" not in bt_args:
        msg = "usage: wayfinder-bt --tree <file.bt> [wayfinder args...]"
        raise InvalidInputError(msg)
    return bt_args, wayfinder_args


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a behavior-tree-backed brain."""
    try:
        raw = list(sys.argv[1:] if argv is None else argv)
        if not raw or raw[0] in {"-h", "--help"}:
            msg = "usage: wayfinder-bt --tree <file.bt> [wayfinder args...]"
            raise InvalidInputError(msg)
        bt_args, wayfinder_args = _split_args(raw)
        bt_parser = argparse.ArgumentParser(prog="wayfinder-bt")
        bt_parser.add_argument("--tree", required=True, help="Path to a .bt JSON tree file")
        parsed = bt_parser.parse_args(bt_args)
        tree_path = Path(parsed.tree)
        brain = BtBrain(tree_path)
        run_cli(wayfinder_args, brain=brain, prog=f"wayfinder-bt --tree {tree_path.name}")
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
