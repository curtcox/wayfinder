"""Executor control loop (§11.1)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.cli.store_paths import resolve_store_root
from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.freshness import evaluate_executable
from wayfinder.core.types import is_terminal_goal_status
from wayfinder.exec.durability import DurabilityStore, PendingAction
from wayfinder.exec.policy import PolicyDecision, check_preconditions, evaluate_policy, load_policy
from wayfinder.exec.shell_exec import (
    build_action_result,
    execute_shell_action,
    noop_action_result,
)
from wayfinder.exec.wayfinder_client import WayfinderClient, WayfinderClientError

NON_INTERACTIVE_EXIT_TYPES = frozenset({"question", "blocked", "unsafe"})
LOOP_DETECT_LIMIT = 5


@dataclass(frozen=True)
class ExecutorConfig:
    """Runtime configuration for the dumb executor."""

    goal_id: str
    store: str | None
    executor_id: str
    wayfinder_command: list[str] | None
    brain_playbook: str | None
    policy_path: Path | None
    dry_run: bool


@dataclass(frozen=True)
class ExecutorOutcome:
    """Final executor result returned to the CLI."""

    stopped_reason: str
    status: dict[str, Any]
    recommendation: dict[str, Any] | None = None


class ExecutorLoop:
    """Drive the §11.1 loop against a wayfinder store."""

    def __init__(self, config: ExecutorConfig) -> None:
        self.config = config
        self.client = WayfinderClient(
            command=config.wayfinder_command,
            store=config.store,
            brain_playbook=config.brain_playbook,
        )
        store_root = resolve_store_root(config.store)
        self._durability = DurabilityStore(store_root, executor_id=config.executor_id)
        self._artifact_store = ArtifactStore.for_goal(store_root, config.goal_id)
        self._policy = load_policy(config.policy_path)
        self._inline_limit = 8192
        self._workspace_uri = ""
        self._loop_stall_count = 0
        self._last_stall_key: str | None = None

    def run(self) -> ExecutorOutcome:
        capabilities = self.client.capabilities()
        limits = capabilities.get("limits", {})
        if isinstance(limits, dict) and "max_inline_output_bytes" in limits:
            self._inline_limit = int(limits["max_inline_output_bytes"])
        status = self.client.status(self.config.goal_id)
        self._workspace_uri = self._workspace_from_status(status)
        verify = self.client.verify(self.config.goal_id)
        if verify.get("ok") is not True:
            msg = "event log verification failed before execution"
            raise WayfinderClientError(msg)
        self._resume_pending()
        return self._loop(status)

    def _workspace_from_status(self, status: dict[str, Any]) -> str:
        workspace = status.get("workspace_uri")
        if isinstance(workspace, str) and workspace:
            return workspace
        events = self.client.history(self.config.goal_id, since_seq=0)
        for event in events:
            if event.get("type") != "goal.created":
                continue
            data = event.get("data", {})
            if isinstance(data, dict):
                goal = data.get("goal", {})
                if isinstance(goal, dict) and isinstance(goal.get("workspace_uri"), str):
                    return str(goal["workspace_uri"])
        msg = "unable to resolve workspace_uri for goal"
        raise WayfinderClientError(msg)

    def _now(self) -> str:
        return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _new_update_id(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(4)}"

    def _actor(self) -> dict[str, Any]:
        return {
            "type": "executor",
            "id": self.config.executor_id,
            "authority": "operator",
            "authenticated": True,
        }

    def _issued_context(self, recommendation_id: str) -> tuple[int, str]:
        events = self.client.history(self.config.goal_id, since_seq=0)
        for event in reversed(events):
            if event.get("type") == "recommendation.issued":
                data = event.get("data", {})
                if not isinstance(data, dict):
                    continue
                recommendation = data.get("recommendation", {})
                if (
                    isinstance(recommendation, dict)
                    and recommendation.get("recommendation_id") == recommendation_id
                ):
                    return int(event["seq"]), str(event["event_hash"])
            if event.get("type") == "recommendation.overridden":
                data = event.get("data", {})
                if not isinstance(data, dict):
                    continue
                override = data.get("override", {})
                replacement = data.get("replacement_recommendation", {})
                if (
                    isinstance(override, dict)
                    and override.get("decision") == "replace"
                    and isinstance(replacement, dict)
                    and replacement.get("recommendation_id") == recommendation_id
                ):
                    return int(event["seq"]), str(event["event_hash"])
        msg = f"issued event not found for recommendation {recommendation_id}"
        raise WayfinderClientError(msg)

    def _find_pending_replacement(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.get("type") != "recommendation.overridden":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            override = data.get("override", {})
            if not isinstance(override, dict) or override.get("decision") != "replace":
                continue
            replacement = data.get("replacement_recommendation")
            if not isinstance(replacement, dict):
                continue
            rec_id = str(replacement.get("recommendation_id", ""))
            if not rec_id:
                continue
            check = evaluate_executable(
                events,
                replacement,
                actor_id=self.config.executor_id,
            )
            if check.has_terminal_action:
                continue
            return replacement
        return None

    def _base_update(
        self,
        *,
        update_id: str,
        recommendation_id: str,
        action_id: str | None,
        issued_event_seq: int,
        issued_event_hash: str,
        update_type: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": update_id,
            "goal_id": self.config.goal_id,
            "recommendation_id": recommendation_id,
            "issued_event_seq": issued_event_seq,
            "issued_event_hash": issued_event_hash,
            "created_at": self._now(),
            "actor": self._actor(),
            "update_type": update_type,
            **extra,
        }
        if action_id is not None:
            body["action_id"] = action_id
        return body

    def _submit_update(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.client.update(self.config.goal_id, body)

    def _resume_pending(self) -> None:
        pending = self._durability.load()
        if pending is None or pending.goal_id != self.config.goal_id:
            return
        if pending.stage == "executed" and pending.action_result is not None:
            body = self._base_update(
                update_id=pending.result_update_id,
                recommendation_id=pending.recommendation_id,
                action_id=pending.action_id,
                issued_event_seq=pending.issued_event_seq,
                issued_event_hash=pending.issued_event_hash,
                update_type="action_result",
                extra={"action_result": pending.action_result},
            )
            self._submit_update(body)
            self._durability.clear()
            return
        if pending.stage in {"accepted", "started"}:
            blocked = {
                "status": "blocked",
                "changed": "unknown",
                "started_at": self._now(),
                "ended_at": self._now(),
                "process": {"exit_code": None, "signal": None, "timed_out": False},
                "output": {
                    "stdout": "",
                    "stderr": "executor interrupted before reporting action result",
                },
            }
            body = self._base_update(
                update_id=pending.result_update_id,
                recommendation_id=pending.recommendation_id,
                action_id=pending.action_id,
                issued_event_seq=pending.issued_event_seq,
                issued_event_hash=pending.issued_event_hash,
                update_type="action_result",
                extra={"action_result": blocked},
            )
            self._submit_update(body)
            self._durability.clear()

    def _loop(self, status: dict[str, Any]) -> ExecutorOutcome:
        while not is_terminal_goal_status(str(status.get("goal_status", "pending"))):
            if self.config.dry_run:
                recommendation = self.client.next(
                    self.config.goal_id,
                    mode="preview",
                    explain="structured",
                )
                return self._handle_dry_run(recommendation, status)

            events = self.client.history(self.config.goal_id, since_seq=0)
            replacement = self._find_pending_replacement(events)
            if replacement is not None:
                outcome = self._handle_recommendation(replacement, status)
                if outcome is not None:
                    return outcome
                status = self.client.status(self.config.goal_id)
                continue

            recommendation = self.client.next(
                self.config.goal_id,
                mode="issue",
                explain="structured",
            )
            outcome = self._handle_recommendation(recommendation, status)
            if outcome is not None:
                return outcome
            status = self.client.status(self.config.goal_id)
        return ExecutorOutcome(stopped_reason="goal_terminal", status=status)

    def _handle_dry_run(
        self,
        recommendation: dict[str, Any],
        status: dict[str, Any],
    ) -> ExecutorOutcome:
        rec_type = str(recommendation.get("recommendation_type", ""))
        if rec_type == "action":
            action = recommendation.get("action", {})
            risk = recommendation.get("risk", {})
            if isinstance(action, dict) and isinstance(risk, dict):
                decision = evaluate_policy(
                    action,
                    risk,
                    policy=self._policy,
                    workspace_uri=self._workspace_uri,
                )
                if decision.denied:
                    return ExecutorOutcome(
                        stopped_reason="policy_denied",
                        status=status,
                        recommendation=recommendation,
                    )
        return ExecutorOutcome(
            stopped_reason="dry_run",
            status=status,
            recommendation=recommendation,
        )

    def _handle_recommendation(
        self,
        recommendation: dict[str, Any],
        status: dict[str, Any],
    ) -> ExecutorOutcome | None:
        rec_type = str(recommendation.get("recommendation_type", ""))
        rec_id = str(recommendation.get("recommendation_id", ""))

        if rec_type == "done":
            issued_seq, issued_hash = self._issued_context(rec_id)
            body = self._base_update(
                update_id=self._new_update_id("upd_done"),
                recommendation_id=rec_id,
                action_id=None,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                update_type="recommendation_disposition",
                extra={"recommendation_disposition": {"disposition": "accepted"}},
            )
            self._submit_update(body)
            return ExecutorOutcome(
                stopped_reason="goal_completed",
                status=self.client.status(self.config.goal_id),
                recommendation=recommendation,
            )

        if rec_type == "wait":
            wait = recommendation.get("wait", {})
            if isinstance(wait, dict) and isinstance(wait.get("until_time"), str):
                until = wait["until_time"]
                target = datetime.fromisoformat(until.replace("Z", "+00:00"))
                now = datetime.now(tz=UTC)
                if now < target:
                    time.sleep(min((target - now).total_seconds(), 300))
            return None

        if rec_type in NON_INTERACTIVE_EXIT_TYPES:
            return ExecutorOutcome(
                stopped_reason=rec_type,
                status=status,
                recommendation=recommendation,
            )

        if rec_type != "action":
            issued_seq, issued_hash = self._issued_context(rec_id)
            body = self._base_update(
                update_id=self._new_update_id("upd_reject"),
                recommendation_id=rec_id,
                action_id=None,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                update_type="recommendation_disposition",
                extra={
                    "recommendation_disposition": {
                        "disposition": "rejected",
                        "reason": "missing capability",
                    },
                },
            )
            self._submit_update(body)
            return None

        return self._handle_action(recommendation, status)

    def _handle_action(
        self,
        recommendation: dict[str, Any],
        status: dict[str, Any],
    ) -> ExecutorOutcome | None:
        rec_id = str(recommendation["recommendation_id"])
        action = recommendation.get("action", {})
        risk = recommendation.get("risk", {})
        if not isinstance(action, dict) or not isinstance(risk, dict):
            msg = "action recommendation missing action or risk"
            raise WayfinderClientError(msg)

        kind = str(action.get("kind", ""))
        if kind not in {"shell", "noop"}:
            self._reject_recommendation(
                recommendation,
                reason="missing capability",
            )
            return ExecutorOutcome(
                stopped_reason="missing_capability",
                status=self.client.status(self.config.goal_id),
                recommendation=recommendation,
            )

        events = self.client.history(self.config.goal_id, since_seq=0)
        check = evaluate_executable(
            events,
            recommendation,
            actor_id=self.config.executor_id,
        )
        if (
            check.has_terminal_action
            or check.superseded
            or not check.fresh
            or check.expired
            or check.claimed_by_other
        ):
            return ExecutorOutcome(
                stopped_reason="stale_recommendation",
                status=status,
                recommendation=recommendation,
            )

        policy_decision = evaluate_policy(
            action,
            risk,
            policy=self._policy,
            workspace_uri=self._workspace_uri,
        )
        if policy_decision.denied:
            self._record_policy_denied(recommendation, policy_decision)
            self._track_loop_stall(rec_id, policy_decision.reason_code or "policy_denied")
            status = self.client.status(self.config.goal_id)
            if status.get("reason_code") == "policy_denied":
                return ExecutorOutcome(
                    stopped_reason="policy_denied",
                    status=status,
                    recommendation=recommendation,
                )
            return None

        preconditions = action.get("preconditions", [])
        if isinstance(preconditions, list) and preconditions:
            precondition_decision = check_preconditions(
                preconditions,
                workspace_uri=self._workspace_uri,
                events=events,
                recommendation_id=rec_id,
            )
            if precondition_decision.denied or precondition_decision.requires_approval:
                if precondition_decision.requires_approval:
                    return ExecutorOutcome(
                        stopped_reason="needs_approval",
                        status=status,
                        recommendation=recommendation,
                    )
                reason_code = precondition_decision.reason_code or "precondition_failed"
                self._record_action_blocked(
                    recommendation,
                    reason_code=reason_code,
                    reason=precondition_decision.reason or "precondition not satisfied",
                )
                self._track_loop_stall(rec_id, reason_code)
                return ExecutorOutcome(
                    stopped_reason="blocked",
                    status=self.client.status(self.config.goal_id),
                    recommendation=recommendation,
                )

        if policy_decision.requires_approval:
            return ExecutorOutcome(
                stopped_reason="needs_approval",
                status=status,
                recommendation=recommendation,
            )

        action_id = str(action.get("action_id", ""))
        issued_seq, issued_hash = self._issued_context(rec_id)
        accept_update_id = self._new_update_id("upd_accept")
        start_update_id = self._new_update_id("upd_start")
        result_update_id = self._new_update_id("upd_result")

        self._durability.save(
            PendingAction(
                goal_id=self.config.goal_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                accept_update_id=accept_update_id,
                start_update_id=start_update_id,
                result_update_id=result_update_id,
                stage="accepted",
            ),
        )
        self._submit_update(
            self._base_update(
                update_id=accept_update_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                update_type="recommendation_disposition",
                extra={"recommendation_disposition": {"disposition": "accepted"}},
            ),
        )

        self._durability.save(
            PendingAction(
                goal_id=self.config.goal_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                accept_update_id=accept_update_id,
                start_update_id=start_update_id,
                result_update_id=result_update_id,
                stage="started",
            ),
        )
        self._submit_update(
            self._base_update(
                update_id=start_update_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                update_type="action_started",
                extra={"action_started": {"started_at": self._now()}},
            ),
        )

        action_result = self._execute_action(action)
        self._durability.save(
            PendingAction(
                goal_id=self.config.goal_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                accept_update_id=accept_update_id,
                start_update_id=start_update_id,
                result_update_id=result_update_id,
                stage="executed",
                action_result=action_result,
            ),
        )
        self._submit_update(
            self._base_update(
                update_id=result_update_id,
                recommendation_id=rec_id,
                action_id=action_id,
                issued_event_seq=issued_seq,
                issued_event_hash=issued_hash,
                update_type="action_result",
                extra={"action_result": action_result},
            ),
        )
        self._durability.clear()
        self._loop_stall_count = 0
        self._last_stall_key = None
        return None

    def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("kind", ""))
        if kind == "noop":
            return noop_action_result()
        if kind == "shell":
            command_result = execute_shell_action(action, workspace_uri=self._workspace_uri)
            return build_action_result(
                command_result,
                action=action,
                artifact_store=self._artifact_store,
                inline_limit=self._inline_limit,
            )
        msg = f"unsupported action kind: {kind}"
        raise WayfinderClientError(msg)

    def _reject_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        rec_id = str(recommendation["recommendation_id"])
        action = recommendation.get("action", {})
        action_id = str(action.get("action_id", "")) if isinstance(action, dict) else ""
        issued_seq, issued_hash = self._issued_context(rec_id)
        body = self._base_update(
            update_id=self._new_update_id("upd_reject"),
            recommendation_id=rec_id,
            action_id=action_id or None,
            issued_event_seq=issued_seq,
            issued_event_hash=issued_hash,
            update_type="recommendation_disposition",
            extra={
                "recommendation_disposition": {
                    "disposition": "rejected",
                    "reason": reason,
                },
            },
        )
        self._submit_update(body)

    def _record_action_blocked(
        self,
        recommendation: dict[str, Any],
        *,
        reason_code: str,
        reason: str,
    ) -> None:
        rec_id = str(recommendation["recommendation_id"])
        action = recommendation.get("action", {})
        action_id = str(action.get("action_id", "")) if isinstance(action, dict) else ""
        issued_seq, issued_hash = self._issued_context(rec_id)
        now = self._now()
        body = self._base_update(
            update_id=self._new_update_id("upd_blocked"),
            recommendation_id=rec_id,
            action_id=action_id or None,
            issued_event_seq=issued_seq,
            issued_event_hash=issued_hash,
            update_type="action_result",
            extra={
                "action_result": {
                    "status": "blocked",
                    "changed": "unknown",
                    "reason_code": reason_code,
                    "started_at": now,
                    "ended_at": now,
                    "process": {"exit_code": None, "signal": None, "timed_out": False},
                    "output": {"stdout": "", "stderr": reason},
                },
            },
        )
        self._submit_update(body)

    def _record_policy_denied(
        self,
        recommendation: dict[str, Any],
        decision: PolicyDecision,
    ) -> None:
        rec_id = str(recommendation["recommendation_id"])
        action = recommendation.get("action", {})
        action_id = str(action.get("action_id", "")) if isinstance(action, dict) else ""
        issued_seq, issued_hash = self._issued_context(rec_id)
        body = self._base_update(
            update_id=self._new_update_id("upd_policy"),
            recommendation_id=rec_id,
            action_id=action_id or None,
            issued_event_seq=issued_seq,
            issued_event_hash=issued_hash,
            update_type="policy_denied",
            extra={
                "policy_denied": {
                    "reason_code": decision.reason_code or "policy_denied",
                    "reason": decision.reason or "denied by executor policy",
                },
            },
        )
        self._submit_update(body)

    def _track_loop_stall(self, recommendation_id: str, reason_code: str) -> None:
        key = f"{recommendation_id}:{reason_code}"
        if key == self._last_stall_key:
            self._loop_stall_count += 1
        else:
            self._loop_stall_count = 1
            self._last_stall_key = key
        if self._loop_stall_count >= LOOP_DETECT_LIMIT:
            msg = "loop detection cap reached for repeated policy denials"
            raise WayfinderClientError(msg)
