"""Store path resolution tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.cli.store_paths import parse_workspace_uri, resolve_store_root, store_for_workspace
from wayfinder.core.errors import InvalidInputError


def test_resolve_store_root_from_explicit(tmp_path: Path) -> None:
    store = tmp_path / "custom-store"
    store.mkdir()
    assert resolve_store_root(str(store)) == store.resolve()


def test_resolve_store_root_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "env-store"
    store.mkdir()
    monkeypatch.setenv("WAYFINDER_STORE", str(store))
    assert resolve_store_root() == store.resolve()


def test_store_for_workspace_defaults_to_dot_wayfinder(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert store_for_workspace(workspace) == (workspace / ".wayfinder").resolve()


def test_parse_workspace_uri_rejects_non_file_scheme() -> None:
    with pytest.raises(InvalidInputError, match="file: URI"):
        parse_workspace_uri("https://example.com/project")


def test_parse_workspace_uri_requires_existing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(InvalidInputError, match="does not exist"):
        parse_workspace_uri(f"file:{missing}")
