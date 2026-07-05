"""Copy canonical Markdown from the repo root into docs/ before each MkDocs build.

The spec and user guide live at the repo root as the single source of truth.
This hook keeps the site in sync without duplicating content in git.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

_CANONICAL = (
    ("README.md", "index.md"),
    ("wayfinder-cli-user-guide.md", "wayfinder-cli-user-guide.md"),
    ("wayfinder-interaction-protocol-v0.1.md", "wayfinder-interaction-protocol-v0.1.md"),
    ("wayfinder-security.md", "wayfinder-security.md"),
)


def _copy_canonical_docs() -> None:
    DOCS.mkdir(exist_ok=True)
    for src_name, dest_name in _CANONICAL:
        shutil.copy2(ROOT / src_name, DOCS / dest_name)


def on_startup(**_kwargs: object) -> None:
    """MkDocs hook: sync canonical root markdown into docs/ before each build."""
    _copy_canonical_docs()
