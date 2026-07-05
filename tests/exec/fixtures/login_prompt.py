"""Interactive login stub for pexpect executor tests."""

from __future__ import annotations

import sys


def main() -> None:
    sys.stdout.write("Username: ")
    sys.stdout.flush()
    sys.stdin.readline()
    sys.stdout.write("Password: ")
    sys.stdout.flush()
    sys.stdin.readline()
    sys.stdout.write("Session established\n")
    sys.stdout.flush()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
