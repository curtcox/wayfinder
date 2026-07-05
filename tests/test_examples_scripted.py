"""Run examples/*/run.sh --scripted in CI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*/run.sh"))


@pytest.mark.parametrize("script", EXAMPLES, ids=[path.parent.name for path in EXAMPLES])
def test_example_scripted(script: Path) -> None:
    proc = subprocess.run(
        ["bash", str(script), "--scripted"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_examples_discovered() -> None:
    assert EXAMPLES, "expected at least one examples/*/run.sh script"
