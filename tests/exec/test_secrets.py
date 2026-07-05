"""Tests for the local secret store."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.secrets import default_secrets_path, load_secrets, resolve_secret_ref


def test_load_secrets_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_secrets(tmp_path / "missing.toml") == {}


def test_resolve_secret_ref_reads_flat_and_nested_entries(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.toml"
    secrets_file.write_text(
        'api_key = "flat-value"\n\n[github]\ntoken = "nested-value"\n',
        encoding="utf-8",
    )
    assert resolve_secret_ref("api_key", secrets_path=secrets_file) == "flat-value"
    assert resolve_secret_ref("github/token", secrets_path=secrets_file) == "nested-value"


def test_resolve_secret_ref_rejects_missing_and_blank_refs(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.toml"
    secrets_file.write_text('api_key = "value"\n', encoding="utf-8")
    with pytest.raises(InvalidInputError, match="non-empty"):
        resolve_secret_ref("   ", secrets_path=secrets_file)
    with pytest.raises(InvalidInputError, match="not found"):
        resolve_secret_ref("missing", secrets_path=secrets_file)


def test_default_secrets_path_uses_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    assert default_secrets_path() == config_home / "wayfinder" / "secrets.toml"
