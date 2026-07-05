"""exec_temporal runner unit tests."""

from __future__ import annotations

from pathlib import Path

from tests.conformance.helpers import goal_create_payload, service_for_store
from wayfinder.exec_temporal.runner import ExecutorRunRequest, run_executor_activity


def test_executor_run_request_round_trip(tmp_path: Path) -> None:
    request = ExecutorRunRequest(
        goal_id="goal_test",
        store=str(tmp_path / "store"),
        executor_id="exec_test",
        wayfinder_command=["wayfinder"],
        brain_playbook=str(tmp_path / "playbook.json"),
        policy_path=str(tmp_path / "policy.yaml"),
        dry_run=True,
    )
    restored = ExecutorRunRequest.from_dict(request.to_dict())
    assert restored == request


def test_run_executor_activity_stub(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    result = run_executor_activity(
        {
            "goal_id": goal_id,
            "store": str(store),
            "dry_run": True,
        },
    )
    assert result["recommendation"]["recommendation_type"] == "action"
