"""Shared executor runner for temporal and stub modes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from wayfinder.exec.loop import ExecutorConfig, ExecutorLoop, ExecutorOutcome


@dataclass(frozen=True)
class ExecutorRunRequest:
    """Serializable executor configuration for Temporal activities."""

    goal_id: str
    store: str | None
    executor_id: str
    wayfinder_command: list[str] | None
    brain_playbook: str | None
    policy_path: str | None
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutorRunRequest:
        wayfinder_command = payload.get("wayfinder_command")
        parsed_command: list[str] | None = None
        if isinstance(wayfinder_command, list):
            parsed_command = [str(item) for item in wayfinder_command]
        policy_path = payload.get("policy_path")
        return cls(
            goal_id=str(payload["goal_id"]),
            store=str(payload["store"]) if payload.get("store") else None,
            executor_id=str(payload.get("executor_id", "wayfinder-exec-temporal-local")),
            wayfinder_command=parsed_command,
            brain_playbook=(
                str(payload["brain_playbook"]) if payload.get("brain_playbook") else None
            ),
            policy_path=str(policy_path) if policy_path else None,
            dry_run=bool(payload.get("dry_run", False)),
        )


def run_executor(request: ExecutorRunRequest) -> ExecutorOutcome:
    """Run the standard executor loop for one goal."""
    config = ExecutorConfig(
        goal_id=request.goal_id,
        store=request.store,
        executor_id=request.executor_id,
        wayfinder_command=request.wayfinder_command,
        brain_playbook=request.brain_playbook,
        policy_path=Path(request.policy_path) if request.policy_path else None,
        dry_run=request.dry_run,
    )
    return ExecutorLoop(config).run()


def run_executor_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Activity body: execute one full executor loop."""
    request = ExecutorRunRequest.from_dict(payload)
    outcome = run_executor(request)
    return {
        "stopped_reason": outcome.stopped_reason,
        "status": outcome.status,
        "recommendation": outcome.recommendation,
    }
