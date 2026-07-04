"""Artifact store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import ArtifactIntegrityError


def test_write_and_verify_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    ref = store.write_bytes(b"hello", artifact_id="art_01", media_type="text/plain")
    store.verify_reference(ref)
    assert ref["sha256"].startswith("sha256:")
    assert store.resolve_uri(ref["uri"]).exists()


def test_rejects_path_escape(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    with pytest.raises(ArtifactIntegrityError):
        store.resolve_uri("file:.wayfinder/goals/goal_01/artifacts/sha256/ab/../escape")
