"""Tests for event hash chain rules."""

from __future__ import annotations

import copy

import pytest

from wayfinder.core.hash_chain import (
    CorruptEventLogError,
    compute_event_hash,
    verify_event_hash,
    verify_hash_chain,
    with_event_hash,
)


def _sample_event(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema": "wip.event/0.1",
        "protocol_version": "0.1",
        "event_id": "evt_00000001",
        "type": "goal.created",
        "time": "2026-07-04T18:00:00Z",
        "goal_id": "goal_01",
        "seq": 1,
        "source": "wayfinder://test",
        "actor": {"type": "human", "id": "test", "authority": "owner"},
        "prev_event_hash": None,
        "event_hash": None,
        "data": {},
    }
    base.update(overrides)
    return base


def test_compute_event_hash_leaves_member_present() -> None:
    event = _sample_event()
    digest = compute_event_hash(event)
    assert digest.startswith("sha256:")
    assert event["event_hash"] is None


def test_with_event_hash_chain() -> None:
    first = with_event_hash(_sample_event(seq=1), prev_event_hash=None)
    assert verify_event_hash(first)
    second_event = _sample_event(event_id="evt_00000002", seq=2)
    second = with_event_hash(second_event, prev_event_hash=first["event_hash"])
    verify_hash_chain([first, second])


def test_verify_hash_chain_detects_tampering() -> None:
    first = with_event_hash(_sample_event(seq=1), prev_event_hash=None)
    second_event = _sample_event(event_id="evt_00000002", seq=2)
    second = with_event_hash(second_event, prev_event_hash=first["event_hash"])
    tampered = copy.deepcopy(second)
    tampered["data"] = {"changed": True}
    with pytest.raises(CorruptEventLogError, match="event_hash mismatch"):
        verify_hash_chain([first, tampered])


def test_verify_hash_chain_detects_seq_gap() -> None:
    first = with_event_hash(_sample_event(seq=1), prev_event_hash=None)
    skipped_event = _sample_event(event_id="evt_00000003", seq=3)
    skipped = with_event_hash(skipped_event, prev_event_hash=first["event_hash"])
    with pytest.raises(CorruptEventLogError, match="invalid event seq ordering"):
        verify_hash_chain([first, skipped])
