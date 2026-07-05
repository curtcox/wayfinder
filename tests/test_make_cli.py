"""wayfinder-make CLI integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conformance.helpers import goal_create_payload, run_cli


def _run_make_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wayfinder.make", *args]
    return subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_report_makefile(workspace: Path) -> None:
    src = workspace / "src"
    src.mkdir(parents=True)
    (src / "data.txt").write_text("hello\n", encoding="utf-8")
    (workspace / "Makefile").write_text(
        "dist/report.pdf: src/data.txt\n\tmkdir -p dist\n\tcp src/data.txt dist/report.pdf\n",
        encoding="utf-8",
    )


def test_make_cli_preview_shows_first_recipe(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = tmp_path / "store"
    _write_report_makefile(workspace)
    created = json.loads(
        run_cli(
            ["--store", str(store), "goal", "create"],
            stdin=json.dumps(goal_create_payload(workspace)),
        ).stdout,
    )
    goal_id = created["result"]["goal"]["goal_id"]
    preview = _run_make_cli(
        [
            "dist/report.pdf",
            "--store",
            str(store),
            "next",
            "--goal-id",
            goal_id,
            "--mode=preview",
            "--explain=summary",
        ],
    )
    assert preview.returncode == 0, preview.stdout + preview.stderr
    recommendation = json.loads(preview.stdout)["result"]
    assert recommendation["recommendation_type"] == "action"
    argv = recommendation["action"]["shell"]["argv"]
    assert argv == ["mkdir", "-p", "dist"]
