"""Synchronize goal event logs with GitHub Issues."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.bridge.gh.client import GitHubClient
from wayfinder.bridge.gh.state import BridgeState, load_state, save_state
from wayfinder.cli.store_paths import resolve_store_root
from wayfinder.core.errors import InvalidInputError, PolicyDeniedError
from wayfinder.exec.wayfinder_client import WayfinderClient


@dataclass(frozen=True)
class AllowlistEntry:
    actor_id: str
    authority: str = "owner"
    authenticated: bool = True

    def to_actor(self) -> dict[str, Any]:
        return {
            "type": "human",
            "id": self.actor_id,
            "authority": self.authority,
            "authenticated": self.authenticated,
        }


@dataclass(frozen=True)
class SyncConfig:
    goal_id: str
    repo: str
    store: str | None
    allowlist_path: Path | None
    wayfinder_command: list[str] | None = None


@dataclass
class SyncResult:
    issue_number: int | None
    events_synced: int
    comments_processed: int
    updates_submitted: int


_APPROVAL_RE = re.compile(r"^\s*approved\b", re.IGNORECASE)


def load_allowlist(path: Path | None) -> dict[str, AllowlistEntry]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = "allowlist must be a JSON object keyed by GitHub login"
        raise InvalidInputError(msg)
    entries: dict[str, AllowlistEntry] = {}
    for login, value in payload.items():
        if not isinstance(value, dict):
            continue
        actor_id = str(value.get("actor_id", login))
        authority = str(value.get("authority", "owner"))
        authenticated = bool(value.get("authenticated", True))
        entries[str(login)] = AllowlistEntry(
            actor_id=actor_id,
            authority=authority,
            authenticated=authenticated,
        )
    return entries


def _goal_description(event: dict[str, Any]) -> str:
    data = event.get("data", {})
    if not isinstance(data, dict):
        return "Wayfinder goal"
    goal = data.get("goal", {})
    if isinstance(goal, dict):
        description = goal.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return "Wayfinder goal"


def _format_recommendation_comment(event: dict[str, Any]) -> str:
    data = event.get("data", {})
    if not isinstance(data, dict):
        return "Recommendation issued."
    recommendation = data.get("recommendation", {})
    if not isinstance(recommendation, dict):
        return "Recommendation issued."
    rec_id = recommendation.get("recommendation_id", "unknown")
    summary = recommendation.get("summary", "")
    lines = [f"**{rec_id}** wants to run: {summary}"]
    action = recommendation.get("action")
    if isinstance(action, dict):
        shell = action.get("shell")
        if isinstance(shell, dict):
            argv = shell.get("argv")
            if isinstance(argv, list) and argv:
                lines.append(f"`{' '.join(str(item) for item in argv)}`")
    risk = recommendation.get("risk")
    if isinstance(risk, dict):
        level = risk.get("level")
        classes = risk.get("classes")
        requires_approval = risk.get("requires_approval")
        risk_bits: list[str] = []
        if isinstance(level, str):
            risk_bits.append(f"risk: {level}")
        if isinstance(classes, list) and classes:
            risk_bits.append(", ".join(str(item) for item in classes))
        if requires_approval:
            risk_bits.append("requires approval")
        if risk_bits:
            lines.append(" · ".join(risk_bits))
    return "\n".join(lines)


def _format_action_result_comment(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", "action.updated"))
    data = event.get("data", {})
    if not isinstance(data, dict):
        return f"{event_type} recorded."
    exit_code = data.get("exit_code")
    changed = data.get("changed")
    parts = [event_type.replace(".", " ")]
    if exit_code is not None:
        parts.append(f"exit {exit_code}")
    if changed is not None:
        parts.append(f"changed: {changed}")
    return ", ".join(parts)


def _open_action_id(events: list[dict[str, Any]], recommendation_id: str) -> str | None:
    for event in reversed(events):
        if event.get("type") != "recommendation.issued":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        recommendation = data.get("recommendation", {})
        if not isinstance(recommendation, dict):
            continue
        if recommendation.get("recommendation_id") != recommendation_id:
            continue
        action = recommendation.get("action")
        if isinstance(action, dict) and action.get("action_id"):
            return str(action["action_id"])
    return None


def _comment_to_update(
    comment_body: str,
    *,
    goal_id: str,
    status: dict[str, Any],
    events: list[dict[str, Any]],
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    text = comment_body.strip()
    if not text:
        return None
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    update_id = f"upd_bridge_{secrets.token_hex(4)}"
    if _APPROVAL_RE.match(text):
        rec_id = status.get("open_recommendation_id")
        if not isinstance(rec_id, str) or not rec_id:
            return None
        action_id = _open_action_id(events, rec_id)
        update: dict[str, Any] = {
            "schema": "wip.update/0.1",
            "protocol_version": "0.1",
            "update_id": update_id,
            "goal_id": goal_id,
            "created_at": now,
            "actor": actor,
            "update_type": "approval",
            "recommendation_id": rec_id,
            "approval": {"decision": "granted", "reason": text},
        }
        if action_id:
            update["action_id"] = action_id
        return update
    return {
        "schema": "wip.update/0.1",
        "protocol_version": "0.1",
        "update_id": update_id,
        "goal_id": goal_id,
        "created_at": now,
        "actor": actor,
        "update_type": "observation",
        "observations": [{"text": text, "effective": {"invalidates": False}}],
    }


def _apply_event_to_github(
    event: dict[str, Any],
    *,
    gh: GitHubClient,
    state: BridgeState,
    goal_id: str,
) -> None:
    event_type = str(event.get("type", ""))
    if event_type == "goal.created":
        if state.issue_number is not None:
            return
        title = _goal_description(event)
        body = f"Wayfinder goal `{goal_id}`"
        issue = gh.create_issue(title=title, body=body)
        state.issue_number = issue.number
        return

    if state.issue_number is None:
        return
    issue_number = state.issue_number

    if event_type == "recommendation.issued":
        gh.add_comment(issue_number, _format_recommendation_comment(event))
        data = event.get("data", {})
        recommendation = data.get("recommendation", {}) if isinstance(data, dict) else {}
        risk = recommendation.get("risk", {}) if isinstance(recommendation, dict) else {}
        if isinstance(risk, dict) and risk.get("requires_approval"):
            gh.add_label(issue_number, "needs-approval")
        return

    if event_type == "approval.granted":
        gh.remove_label(issue_number, "needs-approval")
        gh.add_comment(issue_number, "approval recorded")
        return

    if event_type in {"action.completed", "action.failed", "action.timed_out"}:
        gh.add_comment(issue_number, _format_action_result_comment(event))
        return

    if event_type in {"goal.completed", "goal.cancelled"}:
        gh.close_issue(issue_number)


def sync_once(config: SyncConfig) -> SyncResult:
    """Mirror new goal events to GitHub and ingest allowlisted issue comments."""
    store_root = resolve_store_root(config.store)
    allowlist = load_allowlist(config.allowlist_path)
    wf = WayfinderClient(command=config.wayfinder_command, store=str(store_root))
    gh = GitHubClient(repo=config.repo)
    state = load_state(store_root, config.goal_id)

    events = wf.history(config.goal_id, since_seq=state.last_synced_seq)
    events_synced = 0
    for event in events:
        seq = int(event.get("seq", 0))
        if seq <= state.last_synced_seq:
            continue
        _apply_event_to_github(event, gh=gh, state=state, goal_id=config.goal_id)
        state.last_synced_seq = seq
        events_synced += 1

    comments_processed = 0
    updates_submitted = 0
    if state.issue_number is not None:
        status = wf.status(config.goal_id)
        all_events = wf.history(config.goal_id, since_seq=0)
        for comment in gh.list_comments(state.issue_number):
            if comment.id in state.processed_comment_ids:
                continue
            if comment.user_login == gh.bot_login:
                state.processed_comment_ids.append(comment.id)
                continue
            comments_processed += 1
            entry = allowlist.get(comment.user_login)
            if entry is None:
                state.processed_comment_ids.append(comment.id)
                continue
            update = _comment_to_update(
                comment.body,
                goal_id=config.goal_id,
                status=status,
                events=all_events,
                actor=entry.to_actor(),
            )
            if update is None:
                state.processed_comment_ids.append(comment.id)
                continue
            try:
                wf.update(config.goal_id, update)
                updates_submitted += 1
            except PolicyDeniedError:
                pass
            state.processed_comment_ids.append(comment.id)

    save_state(store_root, config.goal_id, state)
    return SyncResult(
        issue_number=state.issue_number,
        events_synced=events_synced,
        comments_processed=comments_processed,
        updates_submitted=updates_submitted,
    )
