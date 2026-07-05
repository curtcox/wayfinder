"""web_exec unit tests."""

from __future__ import annotations

from pathlib import Path

from wayfinder.exec.web_exec import execute_web_action


def test_execute_web_action_stub_download(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    action = {
        "kind": "shell",
        "title": "Download invoice",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [{"op": "await_download", "filename": "june.pdf"}],
        },
    }
    result = execute_web_action(
        action,
        workspace_uri=f"file:{workspace}",
        force_stub=True,
    )
    assert result.exit_code == 0
    assert (workspace / "june.pdf").is_file()
