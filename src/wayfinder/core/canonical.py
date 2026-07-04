"""RFC 8785 canonical JSON helpers (§2, §6.6)."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

TRANSPORT_ONLY_FIELDS = frozenset({"request_id"})
JsonValue = Any


def strip_transport_fields(
    obj: dict[str, JsonValue],
    *,
    fields: frozenset[str] = TRANSPORT_ONLY_FIELDS,
) -> dict[str, JsonValue]:
    """Return a shallow copy with transport-only fields removed."""
    return {key: value for key, value in obj.items() if key not in fields}


def canonical_bytes(obj: JsonValue) -> bytes:
    """Serialize *obj* with RFC 8785 canonical JSON."""
    return rfc8785.dumps(obj)


def canonical_compare(
    left: JsonValue,
    right: JsonValue,
    *,
    ignore_fields: frozenset[str] = TRANSPORT_ONLY_FIELDS,
) -> bool:
    """Return whether two objects are canonically identical after stripping transport fields."""
    if isinstance(left, dict) and isinstance(right, dict):
        left = strip_transport_fields(left, fields=ignore_fields)
        right = strip_transport_fields(right, fields=ignore_fields)
    return canonical_bytes(left) == canonical_bytes(right)


def sha256_digest(data: bytes) -> str:
    """Return a spec-formatted SHA-256 digest string."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
