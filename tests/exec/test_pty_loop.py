"""PtyExecutorLoop unit tests."""

from __future__ import annotations

from pathlib import Path

from tests.conformance.helpers import goal_create_payload, service_for_store
from wayfinder.exec.loop import ExecutorConfig
from wayfinder.exec.pty_loop import PtyExecutorLoop


def test_pty_executor_loop_dry_run(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    service = service_for_store(store)
    created = service.goal_create(goal_create_payload(workspace))
    goal_id = str(created["goal"]["goal_id"])
    config = ExecutorConfig(
        goal_id=goal_id,
        store=str(store),
        executor_id="pty-test",
        wayfinder_command=None,
        brain_playbook=None,
        policy_path=None,
        dry_run=True,
    )
    outcome = PtyExecutorLoop(config).run()
    assert outcome.recommendation is not None
