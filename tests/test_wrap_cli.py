"""wayfinder-wrap CLI tests."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from wayfinder.wrap.main import main


def test_wrap_main_requires_tool_argument() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["capabilities"])
    assert exc_info.value.code == 1


def test_wrap_capabilities_with_scripted_brain_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYFINDER_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("WAYFINDER_LLM_API_KEY", "unused")
    monkeypatch.setenv("WAYFINDER_LLM_MODEL", "unused")
    cmd = [
        sys.executable,
        "-m",
        "wayfinder.wrap",
        "curl",
        "capabilities",
        "--request-id",
        "req_wrap",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "wip.response/0.1"
    assert payload["request_id"] == "req_wrap"
