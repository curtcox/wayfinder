"""Wayfinder store path resolution (§6.0)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from wayfinder.core.errors import InvalidInputError


def resolve_store_root(explicit: str | None = None) -> Path:
    """Resolve the store root from flag, env, or cwd default."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("WAYFINDER_STORE")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / ".wayfinder").resolve()


def store_for_workspace(workspace: Path, *, explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace / ".wayfinder").resolve()


def parse_workspace_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        msg = "workspace_uri must be an absolute file: URI"
        raise InvalidInputError(msg)
    path = unquote(parsed.path)
    if not path:
        msg = "workspace_uri must reference an existing directory"
        raise InvalidInputError(msg)
    resolved = Path(path).resolve()
    if not resolved.is_dir():
        msg = f"workspace_uri directory does not exist: {resolved}"
        raise InvalidInputError(msg)
    return resolved
