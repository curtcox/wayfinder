"""Additional artifact store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import ArtifactIntegrityError


def test_write_is_idempotent_for_same_content(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    first = store.write_bytes(b"same-bytes", artifact_id="art_a")
    second = store.write_bytes(b"same-bytes", artifact_id="art_b")
    assert first["sha256"] == second["sha256"]
    assert store.resolve_uri(first["uri"]).exists()


def test_write_with_description(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    ref = store.write_bytes(b"payload", artifact_id="art_01", description="demo")
    assert ref["description"] == "demo"


def test_content_address_collision_raises(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    ref = store.write_bytes(b"expected", artifact_id="art_01")
    store.resolve_uri(ref["uri"]).write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError, match="collision"):
        store.write_bytes(b"expected", artifact_id="art_02")


def test_verify_reference_missing_and_mismatch(tmp_path: Path) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    ref = store.write_bytes(b"hello", artifact_id="art_01")
    store.resolve_uri(ref["uri"]).unlink()
    with pytest.raises(ArtifactIntegrityError, match="missing"):
        store.verify_reference(ref)
    store.write_bytes(b"hello", artifact_id="art_01")
    ref["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ArtifactIntegrityError, match="digest mismatch"):
        store.verify_reference(ref)


@pytest.mark.parametrize(
    ("uri", "match"),
    [
        ("https://example.com/artifact", "unsupported artifact URI scheme"),
        ("file:/absolute/path", "absolute file URIs"),
        ("file:.wayfinder/goals/other/artifacts/sha256/ab/cd", "escapes goal artifact root"),
        ("file:.wayfinder/goals/goal_01/artifacts/sha256/ab/../escape", "parent segments"),
    ],
)
def test_resolve_uri_rejects_invalid_paths(tmp_path: Path, uri: str, match: str) -> None:
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    with pytest.raises(ArtifactIntegrityError, match=match):
        store.resolve_uri(uri)
