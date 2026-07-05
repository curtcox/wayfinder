"""Minimal independent lock probe for Appendix B vector 15.33.

Uses only the pinned O_CREAT|O_EXCL append-lock primitive from the core library
to prove cross-implementation mutual exclusion without importing the CLI.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wayfinder.core.errors import StorageConflictError
from wayfinder.core.lock import AppendLock


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-lock probe for conformance 15.33")
    parser.add_argument("--store", required=True, help="Wayfinder store root")
    parser.add_argument("--goal-id", required=True, help="Goal identifier")
    parser.add_argument("--holder", default="lock-probe", help="Lock holder name")
    args = parser.parse_args()

    lock = AppendLock.for_goal(Path(args.store), args.goal_id)
    try:
        with lock.acquire(args.holder):
            pass
    except StorageConflictError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
