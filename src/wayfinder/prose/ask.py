"""Synthesize read-only answers from goal state (§8.3-§8.4)."""

from __future__ import annotations

import json
from typing import Any, Protocol

from wayfinder.llm.client import ChatClient
from wayfinder.prose.context import (
    GoalContext,
    ReadClient,
    gather_goal_context,
    gather_store_context,
)


class WayfinderClientProtocol(ReadClient, Protocol):
    """Minimal client surface used by ask helpers."""

    def explain(self, goal_id: str, recommendation_id: str) -> dict[str, Any]: ...

    def verify(self, goal_id: str) -> dict[str, Any]: ...


def _ask_system_prompt(*, review_mode: bool) -> str:
    if review_mode:
        return (
            "You help a human review a Wayfinder recommendation before approval. "
            "Quote shell argv arrays verbatim — never paraphrase commands. "
            "Cite event seq numbers in brackets like [seq 14]. "
            "Describe risk as declared in the recommendation. "
            "Return plain text, not JSON."
        )
    return (
        "You answer questions about Wayfinder goals using only the supplied context. "
        "Cite event seq numbers in brackets like [seq 14] for every factual claim. "
        "Never invent events or recommendations not present in the context. "
        "Return plain text, not JSON."
    )


def _goal_context_payload(context: GoalContext) -> dict[str, Any]:
    return {
        "goal_id": context.goal_id,
        "status": context.status,
        "goal": context.goal,
        "open_recommendation": context.open_recommendation,
        "recent_events": context.recent_events,
    }


def synthesize_goal_answer(
    client: ChatClient,
    question: str,
    context: GoalContext,
    *,
    recommendation_id: str | None = None,
    explain: dict[str, Any] | None = None,
    verify: dict[str, Any] | None = None,
) -> str:
    """Synthesize a read-only answer for one goal."""
    review_mode = recommendation_id is not None
    payload: dict[str, Any] = {
        "question": question,
        "context": _goal_context_payload(context),
    }
    if recommendation_id is not None:
        payload["review_recommendation_id"] = recommendation_id
    if explain is not None:
        payload["explain"] = explain
    if verify is not None:
        payload["verify"] = verify
    messages = [
        {"role": "system", "content": _ask_system_prompt(review_mode=review_mode)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return client.complete(messages, json_mode=False).strip()


def synthesize_store_answer(
    client: ChatClient,
    question: str,
    summaries: list[dict[str, Any]],
) -> str:
    """Synthesize a store-wide answer across multiple goals."""
    payload = {"question": question, "goals": summaries}
    messages = [
        {"role": "system", "content": _ask_system_prompt(review_mode=False)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    return client.complete(messages, json_mode=False).strip()


def answer_for_goal(
    client: WayfinderClientProtocol,
    llm: ChatClient,
    goal_id: str,
    question: str,
    *,
    recommendation_id: str | None = None,
) -> str:
    """Gather reads and synthesize an answer for one goal."""
    context = gather_goal_context(client, goal_id)
    explain = None
    verify = None
    if recommendation_id is not None:
        explain = client.explain(goal_id, recommendation_id)
    else:
        verify = client.verify(goal_id)
    return synthesize_goal_answer(
        llm,
        question,
        context,
        recommendation_id=recommendation_id,
        explain=explain,
        verify=verify,
    )


def answer_for_store(
    llm: ChatClient,
    question: str,
    *,
    store: str | None = None,
) -> str:
    """Gather store-wide context and synthesize an answer."""
    summaries = gather_store_context(store=store)
    return synthesize_store_answer(llm, question, summaries)
