"""Temporal workflow and activities for durable executor runs (§9.6)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from wayfinder.exec_temporal.runner import ExecutorRunRequest, run_executor_activity


def _require_temporal() -> None:
    try:
        import temporalio  # noqa: F401
    except ImportError as exc:
        msg = (
            "temporalio is required for wayfinder-exec-temporal; "
            "install with the machines extra or set WAYFINDER_TEMPORAL_STUB=1"
        )
        raise RuntimeError(msg) from exc


async def run_temporal_workflow(
    request: ExecutorRunRequest,
    *,
    task_queue: str,
    temporal_address: str,
    workflow_id: str,
) -> dict[str, Any]:
    """Start a Temporal workflow and wait for the executor result."""
    _require_temporal()
    from temporalio import activity, workflow
    from temporalio.client import Client
    from temporalio.worker import Worker

    @activity.defn(name="wayfinder.run_executor")
    def _activity(payload: dict[str, Any]) -> dict[str, Any]:
        return run_executor_activity(payload)

    @workflow.defn(name="wayfinder.ExecutorWorkflow")
    class ExecutorWorkflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            return await workflow.execute_activity(
                _activity,
                payload,
                start_to_close_timeout=timedelta(hours=2),
            )

    client = await Client.connect(temporal_address)
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[ExecutorWorkflow],
        activities=[_activity],
    ):
        handle = await client.start_workflow(
            ExecutorWorkflow.run,
            request.to_dict(),
            id=workflow_id,
            task_queue=task_queue,
        )
        return await handle.result()
