"""wayfinder-web CLI entry point (Phase 7b)."""

from __future__ import annotations

import sys

from wayfinder.brains.web import WebBrain
from wayfinder.cli.main import run_cli
from wayfinder.cli.responses import map_exception, write_json
from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import load_llm_config


def main(argv: list[str] | None = None) -> None:
    """Run the wayfinder CLI with a browser automation brain."""
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        llm_client: ChatClient | None = None
        try:
            llm_client = ChatClient(load_llm_config())
        except Exception:
            llm_client = None
        brain = WebBrain(llm_client=llm_client)
        run_cli(args, brain=brain, prog="wayfinder-web")
    except SystemExit:
        raise
    except BaseException as exc:
        payload, code = map_exception(exc)
        write_json(payload)
        raise SystemExit(code) from exc


if __name__ == "__main__":
    main()
