"""Additional artifact store tests."""

from __future__ import annotations

from pathlib import Path

from wayfinder.core.artifacts import ArtifactStore


def test_write_is_idempotent_for_same_content(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    first = store.write_bytes(b"same-bytes", artifact_id="art_a")
    second = store.write_bytes(b"same-bytes", artifact_id="art_b")
    assert first["sha256"] == second["sha256"]
    assert store.resolve_uri(first["uri"]).exists()
