"""Browser runner invoked via shell.argv (§9.10).

wayfinder-exec-web intercepts x_browser_steps actions and drives Playwright directly.
This module exists so recommendations carry an honest argv for review; invoking it
standalone is not the supported execution path.
"""

from __future__ import annotations

import sys


def main() -> None:
    sys.stderr.write(
        "wayfinder.web.runner is invoked by wayfinder-exec-web, not directly.\n",
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
