#!/usr/bin/env python3
"""Run examples/*/run.sh and capture pass/fail/skip results as JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_example(script: Path, *, scripted: bool, root: Path) -> dict[str, str]:
    args = ["bash", str(script)]
    if scripted:
        args.append("--scripted")
    proc = subprocess.run(
        args,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    combined = (proc.stdout + proc.stderr).strip()
    last_line = combined.splitlines()[-1] if combined else ""
    if proc.returncode == 0 and last_line.startswith("skip "):
        status = "skip"
        detail = last_line
    elif proc.returncode == 0:
        status = "pass"
        detail = last_line
    else:
        status = "fail"
        detail = last_line or f"exit {proc.returncode}"
    return {
        "name": script.parent.name,
        "status": status,
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports-out/examples.json"),
        help="Write results JSON array to this path",
    )
    parser.add_argument(
        "--scripted",
        action="store_true",
        default=True,
        help="Pass --scripted to each example (default)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run examples without --scripted (live mode)",
    )
    args = parser.parse_args()
    root = _repo_root()
    scripted = not args.live
    rows: list[dict[str, str]] = []
    failed = False
    for script in sorted((root / "examples").glob("*/run.sh")):
        label = f"{script} {'--scripted' if scripted else ''}".strip()
        print(f"==> {label}", flush=True)
        row = _run_example(script, scripted=scripted, root=root)
        if row["status"] == "fail":
            failed = True
            print(row["detail"], file=sys.stderr, flush=True)
        elif row["detail"]:
            print(row["detail"], flush=True)
        rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
