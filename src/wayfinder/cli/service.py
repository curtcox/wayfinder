"""Wayfinder command handlers wired to the protocol core."""

from __future__ import annotations

import getpass
import itertools
import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wayfinder import __version__
from wayfinder.brains.base import Brain
from wayfinder.cli.capabilities import build_capabilities
from wayfinder.cli.store_paths import parse_workspace_uri, resolve_store_root, store_for_workspace
from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import (
    InvalidInputError,
    SchemaValidationError,
    StorageConflictError,
)
from wayfinder.core.goal_store import GoalStore
from wayfinder.core.hash_chain import CorruptEventLogError
from wayfinder.core.idempotency import IdempotencyStore
from wayfinder.core.reducer import reduce_events
from wayfinder.core.types import is_terminal_goal_status
from wayfinder.core.validation import validate

GOAL_CREATE_SCHEMA = "wip.goal_create/0.1.json"
UPDATE_SCHEMA = "wip.update/0.1.json"
HOLDER = "wayfinder-cli"
INSTANCE_ID = "wayfinder_local"


class WayfinderService:
    """Implements CLI commands against a local wayfinder store."""

    def __init__(
        self,
        *,
        brain: Brain,
        store_root: Path | None = None,
        instance_id: str = INSTANCE_ID,
    ) -> None:
        self.brain = brain
        self.store_root = store_root
        self.instance_id = instance_id
        self._id_counter = itertools.count(1)

    def _effective_store(self, explicit: str | None) -> str | None:
        if explicit is not None:
            return explicit
        if self.store_root is not None:
            return str(self.store_root)
        return None

    def _store_root(self, explicit: str | None = None) -> Path:
        return resolve_store_root(self._effective_store(explicit))

    def _goal_store(self, goal_id: str, *, store: str | None = None) -> GoalStore:
        root = self._store_root(store)
        goal_dir = root / "goals" / goal_id
        if not goal_dir.exists():
            msg = f"unknown goal_id: {goal_id}"
            raise InvalidInputError(msg)
        return GoalStore(root, goal_id)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(4)}"

    def _event_id(self) -> str:
        return self._new_id("evt")

    def _now(self) -> str:
        return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _enrich_actor(self, actor: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(actor)
        if actor.get("type") == "human":
            try:
                user = getpass.getuser()
                enriched["authenticated"] = actor.get("id") == user
            except OSError:
                enriched["authenticated"] = False
        return enriched

    def _load_goal_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in events:
            if event.get("type") != "goal.created":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            goal = data.get("goal")
            if isinstance(goal, dict):
                return goal
        msg = "goal.created event missing from log"
        raise CorruptEventLogError(msg)

    def capabilities(self) -> dict[str, Any]:
        return build_capabilities(instance_id=self.instance_id)

    def goal_create(self, payload: dict[str, Any], *, store: str | None = None) -> dict[str, Any]:
        try:
            validate(payload, GOAL_CREATE_SCHEMA)
        except SchemaValidationError as exc:
            if payload.get("schema") != "wip.goal_create/0.1":
                msg = "input must be wip.goal_create/0.1"
                raise InvalidInputError(msg) from exc
            raise
        create_id = str(payload["create_id"])
        workspace = parse_workspace_uri(str(payload["workspace_uri"]))
        root = store_for_workspace(workspace, explicit=self._effective_store(store))
        idempotency = IdempotencyStore.for_store(root)
        existing = idempotency.get_create(create_id)
        if existing is not None:
            digest = IdempotencyStore.canonical_hash(payload)
            if existing.canonical_hash != digest:
                msg = f"create_id reused with different content: {create_id}"
                raise InvalidInputError(msg)
            goal_store = GoalStore(root, existing.goal_id)
            events = goal_store.read_events()
            created = next(
                event for event in events if int(event["seq"]) == existing.created_event_seq
            )
            status = goal_store.status(observed_at=self._now())
            return {
                "goal": self._load_goal_from_events(events),
                "events": [created],
                "status": status,
                "replayed": True,
            }

        goal_id = self._new_id("goal")
        actor = self._enrich_actor(payload["actor"])
        goal: dict[str, Any] = {
            "schema": "wip.goal/0.1",
            "protocol_version": "0.1",
            "goal_id": goal_id,
            "created_at": payload["created_at"],
            "actor": actor,
            "description": payload["description"],
            "workspace_uri": payload["workspace_uri"],
            "goal_status": "pending",
            "run_id": None,
        }
        if "policy" in payload:
            goal["policy"] = payload["policy"]
        if "metadata" in payload:
            goal["metadata"] = payload["metadata"]

        event = {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": self._event_id(),
            "type": "goal.created",
            "time": self._now(),
            "goal_id": goal_id,
            "source": f"wayfinder://{self.instance_id}",
            "actor": actor,
            "data": {"goal": goal},
            "run_id": None,
        }
        goal_store = GoalStore(root, goal_id)
        result = goal_store.append_events([event], holder=HOLDER)
        created = result.events[0]
        idempotency.put_create(
            create_id,
            payload,
            goal_id=goal_id,
            event_seq=int(created["seq"]),
        )
        status = goal_store.status(observed_at=self._now())
        return {
            "goal": goal,
            "events": [created],
            "status": status,
            "replayed": False,
        }

    def status(self, goal_id: str, *, store: str | None = None) -> dict[str, Any]:
        goal_store = self._goal_store(goal_id, store=store)
        return goal_store.status(observed_at=self._now())

    def history(
        self,
        goal_id: str,
        *,
        since_seq: int,
        limit: int | None = None,
        store: str | None = None,
    ) -> list[str]:
        goal_store = self._goal_store(goal_id, store=store)
        return goal_store.event_log.read_raw_lines_since(since_seq, limit=limit)

    def history_iter(
        self,
        goal_id: str,
        *,
        since_seq: int,
        limit: int | None = None,
        store: str | None = None,
    ) -> Iterator[str]:
        """Yield verified JSONL history lines for streaming CLI output."""
        goal_store = self._goal_store(goal_id, store=store)
        return goal_store.event_log.iter_verified_lines_since(since_seq, limit=limit)

    def history_page(
        self,
        goal_id: str,
        *,
        since_seq: int,
        limit: int | None = None,
        store: str | None = None,
    ) -> dict[str, Any]:
        """Return a paginated history result for JSON-RPC goal.history."""
        max_page = int(build_capabilities()["limits"]["max_history_events_per_page"])
        effective_limit = min(limit, max_page) if limit is not None else max_page
        goal_store = self._goal_store(goal_id, store=store)
        raw_lines = goal_store.event_log.read_raw_lines_since(
            since_seq,
            limit=effective_limit + 1,
        )
        truncated = len(raw_lines) > effective_limit
        if truncated:
            raw_lines = raw_lines[:effective_limit]
        events = [json.loads(line) for line in raw_lines]
        next_since = int(events[-1]["seq"]) if truncated and events else None
        return {
            "events": events,
            "truncated": truncated,
            "next_since_seq": next_since,
        }

    def update(
        self,
        goal_id: str,
        payload: dict[str, Any],
        *,
        store: str | None = None,
    ) -> dict[str, Any]:
        try:
            validate(payload, UPDATE_SCHEMA)
        except SchemaValidationError as exc:
            if payload.get("schema") != "wip.update/0.1":
                msg = "input must be wip.update/0.1"
                raise InvalidInputError(msg) from exc
            raise
        body_goal_id = str(payload.get("goal_id", goal_id))
        if body_goal_id != goal_id:
            msg = "goal_id flag and update body goal_id differ"
            raise InvalidInputError(msg)
        goal_store = self._goal_store(goal_id, store=store)
        update_id = str(payload["update_id"])
        idempotency = goal_store.idempotency
        existing = idempotency.get_update(update_id)
        replayed = existing is not None
        if replayed:
            if existing is None:
                msg = f"update replay state missing for {update_id}"
                raise InvalidInputError(msg)
            digest = IdempotencyStore.canonical_hash(payload)
            if existing.canonical_hash != digest:
                msg = f"update_id reused with different content: {update_id}"
                raise InvalidInputError(msg)

        payload = {**payload, "actor": self._enrich_actor(payload["actor"])}
        append_result = goal_store.apply_update(
            payload,
            holder=HOLDER,
            event_id_factory=self._event_id,
        )
        status = goal_store.status(observed_at=self._now())
        return {
            "update_id": update_id,
            "appended_events": append_result.events,
            "seq_start": append_result.seq_start,
            "seq_end": append_result.seq_end,
            "event_log_head": status["event_log_head"],
            "status": status,
            "replayed": replayed,
        }

    def next(
        self,
        goal_id: str,
        *,
        mode: str,
        supersede: bool = False,
        explain_mode: str = "none",
        store: str | None = None,
    ) -> dict[str, Any]:
        if mode not in {"preview", "issue"}:
            msg = "mode must be preview or issue"
            raise InvalidInputError(msg)
        goal_store = self._goal_store(goal_id, store=store)
        events = goal_store.read_events()
        state = reduce_events(events)
        if is_terminal_goal_status(state.goal_status):
            msg = f"goal is terminal: {state.goal_status}"
            raise InvalidInputError(msg)
        goal = self._load_goal_from_events(events)
        status = state.to_status(observed_at=self._now())
        open_id = state.open_recommendation_id
        if mode == "issue":
            if open_id is not None and not supersede:
                msg = "open executable recommendation exists; pass --supersede to replace"
                raise StorageConflictError(msg)
        recommendation = self.brain.recommend(
            goal=goal,
            status=status,
            events=events,
            mode=mode,
            explain_mode=explain_mode,
        )
        recommendation = self._finalize_recommendation(
            recommendation,
            goal=goal,
            events=events,
            mode=mode,
            explain_mode=explain_mode,
        )
        if mode == "preview":
            return recommendation

        event_templates: list[dict[str, Any]] = []
        superseded_ids: list[str] = []
        with goal_store.lock.acquire(HOLDER):
            fresh_events = goal_store.read_events()
            fresh_state = reduce_events(fresh_events)
            fresh_open_id = fresh_state.open_recommendation_id
            if fresh_open_id is not None and not supersede:
                msg = "open executable recommendation exists; pass --supersede to replace"
                raise StorageConflictError(msg)
            if fresh_open_id is not None and supersede:
                superseded_ids.append(fresh_open_id)
                event_templates.append(
                    self._superseded_event(
                        goal_id=goal_id,
                        recommendation_id=fresh_open_id,
                        superseded_by=str(recommendation["recommendation_id"]),
                    ),
                )
            recommendation["supersedes"] = superseded_ids
            event_templates.append(
                self._issued_event(goal_id=goal_id, recommendation=recommendation),
            )
            append_result = goal_store.append_while_locked(event_templates)
        issued_event = append_result.events[-1]
        data = issued_event.get("data", {})
        if not isinstance(data, dict):
            msg = "issued event missing data object"
            raise CorruptEventLogError(msg)
        recommendation_payload = data.get("recommendation")
        if not isinstance(recommendation_payload, dict):
            msg = "issued event missing recommendation payload"
            raise CorruptEventLogError(msg)
        if explain_mode != "none" and "explanation" in recommendation:
            recommendation_payload["explanation"] = recommendation["explanation"]
        return recommendation_payload

    def explain(
        self,
        goal_id: str,
        recommendation_id: str,
        *,
        store: str | None = None,
    ) -> dict[str, Any]:
        goal_store = self._goal_store(goal_id, store=store)
        events = goal_store.read_events()
        for event in events:
            if event.get("type") != "recommendation.issued":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            recommendation = data.get("recommendation", {})
            if (
                isinstance(recommendation, dict)
                and recommendation.get("recommendation_id") == recommendation_id
            ):
                explanation = recommendation.get("explanation")
                if not isinstance(explanation, dict):
                    explanation = {
                        "mode": "summary",
                        "summary": recommendation.get("summary", ""),
                        "evidence": [],
                        "redactions": [],
                    }
                return {
                    "schema": "wip.explanation/0.1",
                    "protocol_version": "0.1",
                    "goal_id": goal_id,
                    "recommendation_id": recommendation_id,
                    "explanation": explanation,
                }
        msg = f"unknown recommendation_id: {recommendation_id}"
        raise InvalidInputError(msg)

    def verify(self, goal_id: str, *, store: str | None = None) -> dict[str, Any]:
        goal_store = self._goal_store(goal_id, store=store)
        problems: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        try:
            events = goal_store.read_events()
        except CorruptEventLogError as exc:
            problems.append({"kind": "hash_mismatch", "seq": None, "detail": str(exc)})
        else:
            artifact_store = goal_store.artifacts
            for event in events:
                self._verify_event_artifacts(event, artifact_store, problems)

        head = events[-1]["event_hash"] if events else None
        return {
            "schema": "wip.verify/0.1",
            "protocol_version": "0.1",
            "goal_id": goal_id,
            "ok": not problems,
            "last_event_seq": int(events[-1]["seq"]) if events else 0,
            "event_log_head": head,
            "problems": problems,
        }

    def _verify_event_artifacts(
        self,
        event: dict[str, Any],
        artifact_store: ArtifactStore,
        problems: list[dict[str, Any]],
    ) -> None:
        data = event.get("data")
        if not isinstance(data, dict):
            return
        refs: list[dict[str, Any]] = []
        action_result = data.get("action_result")
        if isinstance(action_result, dict):
            artifacts = action_result.get("artifacts", [])
            if isinstance(artifacts, list):
                refs.extend(item for item in artifacts if isinstance(item, dict))
        for ref in refs:
            try:
                artifact_store.verify_reference(ref)
            except Exception as exc:
                problems.append(
                    {
                        "kind": "artifact_digest_mismatch",
                        "seq": int(event["seq"]),
                        "detail": str(exc),
                    },
                )

    def _finalize_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        goal: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        issued_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_at = (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        head_seq = int(events[-1]["seq"]) if events else 0
        head_hash = str(events[-1]["event_hash"]) if events else None
        rec_id = self._new_id("rec")
        finalized: dict[str, Any] = {
            "schema": "wip.recommendation/0.1",
            "protocol_version": "0.1",
            "goal_id": goal["goal_id"],
            "recommendation_id": rec_id,
            "issued_at": issued_at,
            "parallel": False,
            "supersedes": [],
            "wayfinder": {
                "name": "wayfinder",
                "version": __version__,
                "instance_id": self.instance_id,
            },
            "basis": {
                "event_log_seq": head_seq,
                "event_log_head": head_hash,
                "state_version": f"scripted-{head_seq}",
            },
            "expires_at": expires_at,
            "run_id": None,
            **recommendation,
        }
        rec_type = str(finalized.get("recommendation_type", "action"))
        if rec_type == "action":
            action = finalized.setdefault("action", {})
            if isinstance(action, dict) and "action_id" not in action:
                action["action_id"] = self._new_id("act")
        if mode == "preview":
            finalized["executable"] = False
            finalized.pop("lease", None)
        elif rec_type == "action":
            finalized["executable"] = True
            finalized["lease"] = {
                "lease_id": self._new_id("lease"),
                "lease_expires_at": expires_at,
            }
        else:
            finalized["executable"] = False
            finalized.pop("lease", None)
        if explain_mode == "none":
            finalized.pop("explanation", None)
        return finalized

    def _issued_event(
        self,
        *,
        goal_id: str,
        recommendation: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": self._event_id(),
            "type": "recommendation.issued",
            "time": self._now(),
            "goal_id": goal_id,
            "source": f"wayfinder://{self.instance_id}",
            "actor": {
                "type": "wayfinder",
                "id": self.instance_id,
                "authority": "operator",
            },
            "data": {"recommendation": recommendation},
            "run_id": None,
        }

    def _superseded_event(
        self,
        *,
        goal_id: str,
        recommendation_id: str,
        superseded_by: str,
    ) -> dict[str, Any]:
        return {
            "schema": "wip.event/0.1",
            "protocol_version": "0.1",
            "event_id": self._event_id(),
            "type": "recommendation.superseded",
            "time": self._now(),
            "goal_id": goal_id,
            "source": f"wayfinder://{self.instance_id}",
            "actor": {
                "type": "wayfinder",
                "id": self.instance_id,
                "authority": "operator",
            },
            "data": {
                "recommendation_id": recommendation_id,
                "superseded_by": superseded_by,
                "reason": "superseded by wayfinder next --supersede",
            },
            "run_id": None,
        }
