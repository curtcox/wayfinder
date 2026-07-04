"""Event hash chain computation and verification (§6.6)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from wayfinder.core.canonical import canonical_bytes, sha256_digest

SHA256_PREFIX = "sha256:"
EventDict = dict[str, Any]


def compute_event_hash(event: EventDict) -> str:
    """Compute the event_hash for an event dict (event_hash must be null during hashing)."""
    payload = deepcopy(event)
    payload["event_hash"] = None
    return sha256_digest(canonical_bytes(payload))


def with_event_hash(event: EventDict, *, prev_event_hash: str | None) -> EventDict:
    """Return *event* with prev_event_hash and event_hash populated."""
    stamped = deepcopy(event)
    stamped["prev_event_hash"] = prev_event_hash
    stamped["event_hash"] = compute_event_hash(stamped)
    return stamped


def verify_event_hash(event: EventDict) -> bool:
    """Return whether the stored event_hash matches the canonical computation."""
    stored = event.get("event_hash")
    if not isinstance(stored, str) or not stored.startswith(SHA256_PREFIX):
        return False
    return stored == compute_event_hash(event)


def verify_hash_chain(events: list[EventDict]) -> None:
    """Validate seq monotonicity and the hash chain for an ordered event list."""
    if not events:
        return

    expected_seq = 1
    prev_hash: str | None = None

    for event in events:
        seq = event.get("seq")
        if not isinstance(seq, int):
            msg = "event seq must be an integer"
            raise CorruptEventLogError(msg)
        if seq != expected_seq:
            msg = f"invalid event seq ordering: expected {expected_seq}, got {seq}"
            raise CorruptEventLogError(msg)
        expected_seq = seq + 1

        if seq == 1:
            if event.get("prev_event_hash") is not None:
                msg = "first event prev_event_hash must be null"
                raise CorruptEventLogError(msg)
        elif event.get("prev_event_hash") != prev_hash:
            msg = "prev_event_hash mismatch"
            raise CorruptEventLogError(msg)

        if not verify_event_hash(event):
            msg = "event_hash mismatch"
            raise CorruptEventLogError(msg)

        prev_hash = event["event_hash"]


class CorruptEventLogError(Exception):
    """Raised when an event log fails hash-chain or seq validation."""
