"""Update mapping coverage for §6.3 branches."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wayfinder.core.errors import InvalidInputError, PolicyDeniedError
from wayfinder.core.hash_chain import with_event_hash
from wayfinder.core.updates import map_update_to_events

NOW = datetime(2026, 7, 4, 18, 5, tzinfo=UTC)
RECOMMENDATION = {
    "recommendation_id": "rec_01",
    "executable": True,
    "expires_at": "2099-01-01T00:00:00Z",
    "lease": {"lease_expires_at": "2099-01-01T00:00:00Z"},
    "action": {"action_id": "act_01"},
    "recommendation_type": "action",
}


def _events() -> list[dict[str, object]]:
    created = with_event_hash(
        {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": "evt_1",
            "type": "goal.created",
            "time": "2026-07-04T18:00:00Z",
            "goal_id": "goal_01",
            "seq": 1,
            "source": "wayfinder://test",
            "actor": {"type": "human", "id": "curt", "authority": "owner"},
            "data": {"goal": {"goal_status": "pending"}},
        },
        prev_event_hash=None,
    )
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
            "data": {"recommendation": RECOMMENDATION},
        },
        prev_event_hash=str(created["event_hash"]),
    )
    return [created, issued]


def _event_id(counter: list[int]) -> str:
    counter[0] += 1
    return f"evt_new_{counter[0]}"


def test_disposition_rejected() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_rej",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "rejected", "reason": "no"},
    }
    mapped = map_update_to_events(
        update,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "recommendation.rejected"


def test_disposition_accepted() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_acc",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "action_id": "act_01",
        "issued_event_seq": 2,
        "issued_event_hash": _events()[1]["event_hash"],
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "accepted"},
    }
    mapped = map_update_to_events(
        update,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "recommendation.accepted"
    assert mapped[0]["invalidates_open_recommendations"] is False


def test_goal_cancel_with_owner() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_cancel",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner", "authenticated": True},
        "update_type": "goal_cancel",
        "goal_cancel": {"reason": "done"},
    }
    mapped = map_update_to_events(
        update,
        events=_events(),
        recommendation=None,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "goal.cancelled"


def test_override_mark_done() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_override",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner", "authenticated": True},
        "update_type": "override",
        "override": {"decision": "mark_done", "reason": "human says done"},
    }
    mapped = map_update_to_events(
        update,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "recommendation.overridden"
    assert mapped[1]["type"] == "goal.completed"


def test_redaction_and_question_answer() -> None:
    counter = [0]
    redaction = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_redact",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "operator", "authenticated": True},
        "update_type": "redaction",
        "redaction": {"target_artifact_id": "art_01", "reason": "secret"},
    }
    mapped = map_update_to_events(
        redaction,
        events=_events(),
        recommendation=None,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "redaction.recorded"

    answer = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_answer",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "operator", "authenticated": True},
        "update_type": "question_answer",
        "question_answer": {"question_id": "q_01", "answer": "blue"},
    }
    mapped = map_update_to_events(
        answer,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "question.answered"


def test_approval_granted_and_skipped_disposition() -> None:
    counter = [0]
    approval = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_appr",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "action_id": "act_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner", "authenticated": True},
        "update_type": "approval",
        "approval": {"decision": "granted", "reason": "ok"},
    }
    mapped = map_update_to_events(
        approval,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["type"] == "approval.granted"

    skipped = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_skip",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "skipped"},
    }
    mapped = map_update_to_events(
        skipped,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[0]["data"]["disposition"] == "skipped"


def test_override_mark_failed() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_fail",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner", "authenticated": True},
        "update_type": "override",
        "override": {"decision": "mark_failed", "reason": "give up"},
    }
    mapped = map_update_to_events(
        update,
        events=_events(),
        recommendation=RECOMMENDATION,
        event_id_factory=lambda: _event_id(counter),
        now=NOW,
    )
    assert mapped[1]["data"]["terminal_status"] == "failed"


def test_expired_disposition_requires_expiry() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_exp",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator", "authenticated": True},
        "update_type": "recommendation_disposition",
        "recommendation_disposition": {"disposition": "expired"},
    }
    with pytest.raises(InvalidInputError):
        map_update_to_events(
            update,
            events=_events(),
            recommendation=RECOMMENDATION,
            event_id_factory=lambda: _event_id(counter),
            now=NOW,
        )


def test_override_requires_authority() -> None:
    counter = [0]
    update = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_bad",
        "goal_id": "goal_01",
        "recommendation_id": "rec_01",
        "created_at": "2026-07-04T18:05:00Z",
        "actor": {"type": "human", "id": "guest", "authority": "observer", "authenticated": True},
        "update_type": "override",
        "override": {"decision": "reject", "reason": "nope"},
    }
    with pytest.raises(PolicyDeniedError):
        map_update_to_events(
            update,
            events=_events(),
            recommendation=RECOMMENDATION,
            event_id_factory=lambda: _event_id(counter),
            now=NOW,
        )
