"""LLM endpoint configuration from environment and config file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from wayfinder.llm.errors import LLMConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "wayfinder" / "config.toml"


@dataclass(frozen=True)
class LLMConfig:
    """OpenAI-compatible chat-completions endpoint settings."""

    base_url: str
    api_key: str
    model: str


def _read_config_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    llm_section = parsed.get("llm")
    if isinstance(llm_section, dict):
        return llm_section
    return {}


def load_llm_config(*, config_path: Path | None = None) -> LLMConfig:
    """Load LLM settings from environment variables, then config file."""
    file_values = _read_config_file(config_path or DEFAULT_CONFIG_PATH)

    def _lookup(name: str) -> str | None:
        env_key = f"WAYFINDER_LLM_{name.upper()}"
        env_value = os.environ.get(env_key)
        if env_value:
            return env_value
        file_value = file_values.get(name.lower())
        if isinstance(file_value, str) and file_value:
            return file_value
        return None

    base_url = _lookup("base_url")
    api_key = _lookup("api_key")
    model = _lookup("model")
    missing = [
        name
        for name, value in [("BASE_URL", base_url), ("API_KEY", api_key), ("MODEL", model)]
        if not value
    ]
    if not base_url or not api_key or not model:
        msg = (
            "LLM configuration incomplete; set "
            + ", ".join(f"WAYFINDER_LLM_{name}" for name in missing)
            + " or add [llm] entries to ~/.config/wayfinder/config.toml"
        )
        raise LLMConfigError(msg)
    return LLMConfig(base_url=base_url.rstrip("/"), api_key=api_key, model=model)
