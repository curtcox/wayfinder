"""Broad core integration tests for coverage and behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from wayfinder.core.goal_store import GoalStore
from wayfinder.core.hash_chain import with_event_hash
from wayfinder.core.idempotency import IdempotencyStore
from wayfinder.core.reducer import reduce_events
from wayfinder.core.updates import map_update_to_events


def _stamp(events: list[dict[str, object]]) -> list[dict[str, object]]:
    stamped: list[dict[str, object]] = []
    prev: str | None = None
    for index, event in enumerate(events, start=1):
        stamped.append(with_event_hash({**event, "seq": index}, prev_event_hash=prev))
        prev = str(stamped[-1]["event_hash"])
    return stamped


def test_goal_store_status_after_seed(tmp_path: Path) -> None:
    store = GoalStore(tmp_path, "goal_01")
    store.append_events(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "curt", "authority": "owner"},
                "data": {"goal": {"goal_id": "goal_01", "goal_status": "pending"}},
            },
        ],
        holder="test",
    )
    status = store.status(observed_at="2026-07-04T18:00:01Z")
    assert status["goal_status"] == "pending"
    assert status["last_event_seq"] == 1


def test_idempotency_round_trip(tmp_path: Path) -> None:
    store = IdempotencyStore.for_store(tmp_path)
    payload = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_x",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "owner"},
        "update_type": "observation",
        "observations": [],
    }
    store.put_update("upd_x", payload, goal_id="goal_01", seq_start=2, seq_end=2)
    record = store.get_update("upd_x")
    assert record is not None
    assert record.seq_start == 2


def test_reducer_clears_open_on_terminal_action() -> None:
    events = _stamp(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "curt", "authority": "owner"},
                "data": {"goal": {"goal_status": "pending"}},
            },
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_2",
                "type": "recommendation.issued",
                "time": "2026-07-04T18:01:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "wayfinder", "id": "wf", "authority": "operator"},
                "data": {
                    "recommendation": {
                        "recommendation_id": "rec_01",
                        "executable": True,
                        "goal_status": "running",
                        "recommendation_type": "action",
                    },
                },
            },
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_3",
                "type": "action.failed",
                "time": "2026-07-04T18:02:00Z",
                "goal_id": "goal_01",
                "source": "executor://exec",
                "actor": {"type": "executor", "id": "exec", "authority": "operator"},
                "data": {
                    "recommendation_id": "rec_01",
                    "action_id": "act_01",
                    "action_result": {"status": "failed"},
                },
            },
        ],
    )
    state = reduce_events(events)
    assert state.open_recommendation_id is None
    assert state.completed_steps == 1


def test_reducer_policy_denied_blocks_goal() -> None:
    events = _stamp(
        [
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_1",
                "type": "goal.created",
                "time": "2026-07-04T18:00:00Z",
                "goal_id": "goal_01",
                "source": "wayfinder://test",
                "actor": {"type": "human", "id": "curt", "authority": "owner"},
                "data": {"goal": {"goal_status": "pending"}},
            },
            {
                "schema": "wip.event/0.1",
                "protocol_version": "0.1",
                "event_id": "evt_2",
                "type": "executor.policy_denied",
                "time": "2026-07-04T18:01:00Z",
                "goal_id": "goal_01",
                "source": "executor://exec",
                "actor": {"type": "executor", "id": "exec", "authority": "operator"},
                "data": {"policy_denied": {"reason_code": "policy_denied", "reason": "nope"}},
            },
        ],
    )
    state = reduce_events(events)
    assert state.goal_status == "blocked"
    assert state.reason_code == "policy_denied"


def test_map_policy_denied_and_correction() -> None:
    counter = 0

    def event_id() -> str:
        nonlocal counter
        counter += 1
        return f"evt_{counter}"

    policy = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_pd",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "executor", "id": "exec", "authority": "operator"},
        "update_type": "policy_denied",
        "policy_denied": {"reason_code": "policy_denied", "reason": "blocked"},
    }
    mapped = map_update_to_events(policy, events=[], recommendation=None, event_id_factory=event_id)
    assert mapped[0]["type"] == "executor.policy_denied"

    correction = {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": "upd_corr",
        "goal_id": "goal_01",
        "created_at": "2026-07-04T18:00:00Z",
        "actor": {"type": "human", "id": "curt", "authority": "operator", "authenticated": True},
        "update_type": "correction",
        "correction": {
            "scope": "observation",
            "target_id": "obs_1",
            "replacement": {"kind": "message", "text": "fixed"},
            "reason": "typo",
        },
    }
    mapped = map_update_to_events(
        correction,
        events=[],
        recommendation=None,
        event_id_factory=event_id,
        now=datetime(2026, 7, 4, 18, 0, tzinfo=UTC),
    )
    assert mapped[0]["type"] == "correction.recorded"
