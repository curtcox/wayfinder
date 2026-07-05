"""Make brain unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.brains.make import MakeBrain, _parse_dry_run
from wayfinder.core.errors import InvalidInputError


def test_parse_dry_run_skips_make_meta_lines() -> None:
    output = (
        "mkdir -p dist\n"
        "make[1]: Entering directory '/tmp'\n"
        "cp src/data.txt dist/report.pdf\n"
        "make[1]: Leaving directory '/tmp'\n"
    )
    assert _parse_dry_run(output) == [
        ["mkdir", "-p", "dist"],
        ["cp", "src/data.txt", "dist/report.pdf"],
    ]


def test_make_brain_issues_first_recipe(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    src = workspace / "src"
    src.mkdir(parents=True)
    (src / "data.txt").write_text("hello\n", encoding="utf-8")
    (workspace / "Makefile").write_text(
        "dist/report.pdf: src/data.txt\n\tmkdir -p dist\n\tcp src/data.txt dist/report.pdf\n",
        encoding="utf-8",
    )
    goal = {
        "goal_id": "goal_make_01",
        "workspace_uri": f"file:{workspace}",
        "description": "Bring the quarterly report up to date.",
    }
    brain = MakeBrain("dist/report.pdf")
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "pending"},
        events=[],
        mode="issue",
        explain_mode="summary",
    )
    assert recommendation["recommendation_type"] == "action"
    action = recommendation["action"]
    assert isinstance(action, dict)
    shell = action["shell"]
    assert isinstance(shell, dict)
    assert shell["argv"] == ["mkdir", "-p", "dist"]


def test_make_brain_done_when_target_fresh(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    dist = workspace / "dist"
    src = workspace / "src"
    dist.mkdir(parents=True)
    src.mkdir(parents=True)
    (src / "data.txt").write_text("hello\n", encoding="utf-8")
    (dist / "report.pdf").write_text("hello\n", encoding="utf-8")
    (workspace / "Makefile").write_text(
        "dist/report.pdf: src/data.txt\n\tcp src/data.txt dist/report.pdf\n",
        encoding="utf-8",
    )
    goal = {
        "goal_id": "goal_make_02",
        "workspace_uri": f"file:{workspace}",
        "description": "Report is already built.",
    }
    brain = MakeBrain("dist/report.pdf")
    recommendation = brain.recommend(
        goal=goal,
        status={"goal_status": "running", "completed_steps": 1},
        events=[],
        mode="issue",
        explain_mode="none",
    )
    assert recommendation["recommendation_type"] == "done"


def test_make_brain_requires_makefile(tmp_path: Path) -> None:
    workspace = tmp_path / "empty"
    workspace.mkdir()
    goal = {
        "goal_id": "goal_make_03",
        "workspace_uri": f"file:{workspace}",
        "description": "No makefile here.",
    }
    brain = MakeBrain("all")
    with pytest.raises(InvalidInputError, match="Makefile"):
        brain.recommend(
            goal=goal,
            status={"goal_status": "pending"},
            events=[],
            mode="issue",
            explain_mode="none",
        )
