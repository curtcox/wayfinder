"""wayfinder doctor CLI tests."""

from __future__ import annotations

import json

from tests.conformance.helpers import run_cli


def test_doctor_returns_checks() -> None:
    proc = run_cli(["doctor"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["result"]["schema"] == "wip.doctor_result/0.1"
    checks = payload["result"]["checks"]
    assert isinstance(checks, list)
    assert any(check["id"] == "wayfinder.version" for check in checks)
    assert "summary" in payload["result"]
