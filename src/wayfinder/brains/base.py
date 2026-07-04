"""Brain protocol for recommendation generation."""

from __future__ import annotations

from typing import Any, Protocol


class Brain(Protocol):
    """Produces the next recommendation from reduced goal state."""

    def recommend(
        self,
        *,
        goal: dict[str, Any],
        status: dict[str, Any],
        events: list[dict[str, Any]],
        mode: str,
        explain_mode: str,
    ) -> dict[str, Any]:
        """Return a wip.recommendation/0.1-shaped object (without issuance metadata)."""
