"""Local secret store for resolving send_secret_ref at execution time."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from wayfinder.core.errors import InvalidInputError


def default_secrets_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(config_home) / "wayfinder" / "secrets.toml"


def _lookup_nested(data: dict[str, Any], ref: str) -> str | None:
    direct = data.get(ref)
    if isinstance(direct, str):
        return direct
    if "/" not in ref:
        return None
    section, key = ref.split("/", 1)
    section_data = data.get(section)
    if isinstance(section_data, dict):
        nested = section_data.get(key)
        if isinstance(nested, str):
            return nested
    return None


def load_secrets(path: Path | None = None) -> dict[str, Any]:
    """Load secrets from *path* or the default secrets.toml location."""
    resolved = path if path is not None else default_secrets_path()
    if not resolved.is_file():
        return {}
    return tomllib.loads(resolved.read_text(encoding="utf-8"))


def resolve_secret_ref(ref: str, *, secrets_path: Path | None = None) -> str:
    """Resolve a secret reference to its plaintext value."""
    if not ref.strip():
        msg = "send_secret_ref must be a non-empty string"
        raise InvalidInputError(msg)
    data = load_secrets(secrets_path)
    value = _lookup_nested(data, ref)
    if value is None:
        msg = f"secret reference not found: {ref}"
        raise InvalidInputError(msg)
    return value
