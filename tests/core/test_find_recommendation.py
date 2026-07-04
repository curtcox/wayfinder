"""Tests for recommendation lookup helpers."""

from __future__ import annotations

from wayfinder.core.hash_chain import with_event_hash
from wayfinder.core.updates import find_recommendation_by_issue


def test_find_recommendation_by_issue() -> None:
    issued = with_event_hash(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": "evt_2",
            "type": "recommendation.issued",
            "time": "2026-07-04T18:01:00Z",
            "goal_id": "goal_01",
            "seq": 2,
            "source": "wayfinder://test",
            "actor": {"type": "wayfinder", "id": "wf", "authority": "operator"},
            "data": {
                "recommendation": {
                    "recommendation_id": "rec_01",
                    "executable": True,
                },
            },
        },
        prev_event_hash="sha256:" + "a" * 64,
    )
    found = find_recommendation_by_issue(
        [issued],
        issued_event_seq=2,
        issued_event_hash=str(issued["event_hash"]),
    )
    assert found is not None
    assert found["recommendation_id"] == "rec_01"
