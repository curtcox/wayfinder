"""web_exec unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wayfinder.core.artifacts import ArtifactStore
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.shell_exec import CommandResult
from wayfinder.exec.web_exec import (
    _browser_steps,
    _browserbase_cdp_url,
    _resolve_download_path,
    _step_value,
    build_web_action_result,
    execute_web_action,
)


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


def test_execute_web_action_stub_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WAYFINDER_WEB_STUB", "1")
    action = {
        "kind": "shell",
        "title": "Screenshot",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [{"op": "screenshot", "filename": "page.png"}],
        },
    }
    result = execute_web_action(action, workspace_uri=f"file:{workspace}")
    assert result.exit_code == 0
    assert (workspace / "page.png").is_file()


def test_browser_steps_requires_table() -> None:
    with pytest.raises(InvalidInputError, match="x_browser_steps"):
        _browser_steps({})


def test_browser_steps_rejects_malformed_step() -> None:
    with pytest.raises(InvalidInputError, match="x_browser_steps\\[0\\]"):
        _browser_steps({"x_browser_steps": [{}]})


def test_resolve_download_path_uses_basename(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    dest = _resolve_download_path(workspace, {"filename": "../nested/invoice.pdf"})
    assert dest == workspace / "invoice.pdf"


def test_execute_web_action_rejects_missing_shell() -> None:
    with pytest.raises(ValueError, match="shell object"):
        execute_web_action({"kind": "shell"}, workspace_uri="file:/tmp")


def test_build_web_action_result_with_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    download = workspace / "invoice.pdf"
    download.write_bytes(b"pdf-bytes")
    store = ArtifactStore.for_goal(tmp_path, "goal_01")
    action = {
        "kind": "shell",
        "title": "Download",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [{"op": "await_download", "filename": "invoice.pdf"}],
        },
    }
    command_result = CommandResult(
        exit_code=0,
        signal=None,
        timed_out=False,
        stdout=b"backend=stub\nstep[0] op=await_download\ndownloaded=invoice.pdf\nsession_id=sess_1",
        stderr=b"",
        started_at="2026-07-04T18:00:00Z",
        ended_at="2026-07-04T18:00:01Z",
    )
    result = build_web_action_result(
        command_result,
        action=action,
        artifact_store=store,
        inline_limit=1024,
        workspace_uri=f"file:{workspace}",
    )
    output = result["output"]
    assert output["browser_session_id"] == "sess_1"
    assert "browser_transcript_artifact" in output
    assert output["downloads"]


def test_build_web_action_result_inline_transcript(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    action = {"kind": "shell", "shell": {"x_browser_steps": []}}
    command_result = CommandResult(
        exit_code=0,
        signal=None,
        timed_out=False,
        stdout=b"short transcript",
        stderr=b"",
        started_at="2026-07-04T18:00:00Z",
        ended_at="2026-07-04T18:00:01Z",
    )
    result = build_web_action_result(
        command_result,
        action=action,
        artifact_store=None,
        inline_limit=1024,
        workspace_uri=f"file:{workspace}",
    )
    assert result["output"]["browser_transcript"] == "short transcript"


def test_execute_web_action_falls_back_to_stub_without_playwright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.delenv("WAYFINDER_WEB_STUB", raising=False)
    action = {
        "kind": "shell",
        "title": "Download",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [{"op": "await_download", "filename": "fallback.pdf"}],
        },
    }
    monkeypatch.setattr(
        "wayfinder.exec.web_exec._require_playwright",
        lambda: (_ for _ in ()).throw(InvalidInputError("no playwright")),
    )
    result = execute_web_action(action, workspace_uri=f"file:{workspace}")
    assert result.exit_code == 0
    assert (workspace / "fallback.pdf").is_file()


def test_step_value_resolves_secret_ref(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.toml"
    secrets_path.write_text('[vendor]\npass = "s3cr3t"\n', encoding="utf-8")
    redactions: list[str] = []
    value = _step_value(
        {"secret_ref": "vendor/pass"},
        secrets_path=secrets_path,
        redaction_values=redactions,
    )
    assert value == "s3cr3t"
    assert redactions == ["s3cr3t"]


def test_run_steps_playwright_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[str] = []

    class FakeDownload:
        def save_as(self, path: str | Path) -> None:
            Path(path).write_bytes(b"download")

    class FakePage:
        def goto(self, url: str, **_: object) -> None:
            events.append(f"goto:{url}")

        def fill(self, selector: str, value: str) -> None:
            events.append(f"fill:{selector}:{value}")

        def click(self, selector: str) -> None:
            events.append(f"click:{selector}")

        def screenshot(self, *, path: str) -> None:
            Path(path).write_bytes(b"png")
            events.append(f"screenshot:{path}")

        def expect_download(self, **_: object) -> object:
            class _Ctx:
                def __enter__(self) -> _Ctx:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                @property
                def value(self) -> FakeDownload:
                    return FakeDownload()

            return _Ctx()

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_context(self, **_: object) -> FakeContext:
            return FakeContext()

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch(self, **_: object) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "wayfinder.exec.web_exec._require_playwright", lambda: lambda: FakeSyncPlaywright()
    )
    monkeypatch.delenv("WAYFINDER_WEB_STUB", raising=False)
    action = {
        "kind": "shell",
        "title": "Browse",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [
                {"op": "navigate", "url": "https://example.com"},
                {"op": "fill", "selector": "#q", "value": "wayfinder"},
                {"op": "click", "selector": "#go"},
                {"op": "screenshot", "filename": "shot.png"},
                {"op": "await_download", "filename": "file.bin", "selector": "#dl"},
            ],
        },
    }
    result = execute_web_action(action, workspace_uri=f"file:{workspace}")
    assert result.exit_code == 0
    assert (workspace / "shot.png").is_file()
    assert (workspace / "file.bin").is_file()
    transcript = result.stdout.decode("utf-8")
    assert "backend=local_playwright" in transcript


def test_run_steps_stub_ignores_unknown_ops(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    action = {
        "kind": "shell",
        "shell": {
            "argv": ["python", "-m", "wayfinder.web.runner"],
            "x_browser_steps": [{"op": "navigate", "url": "https://example.com"}],
        },
    }
    result = execute_web_action(action, workspace_uri=f"file:{workspace}", force_stub=True)
    assert result.exit_code == 0


def test_browserbase_cdp_url_returns_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    assert _browserbase_cdp_url() is None


def test_browserbase_cdp_url_parses_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "proj")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"connectUrl": "wss://cdp.example", "id": "sess_123"}

    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: FakeResponse())
    assert _browserbase_cdp_url() == ("wss://cdp.example", "sess_123")


def test_playwright_rejects_unsupported_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FakePage:
        def goto(self, *_a: object, **_k: object) -> None:
            return None

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_context(self, **_k: object) -> FakeContext:
            return FakeContext()

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch(self, **_k: object) -> FakeBrowser:
            return FakeBrowser()

        def connect_over_cdp(self, _url: str) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "wayfinder.exec.web_exec._require_playwright", lambda: lambda: FakeSyncPlaywright()
    )
    monkeypatch.delenv("WAYFINDER_WEB_STUB", raising=False)
    action = {
        "kind": "shell",
        "shell": {
            "argv": ["runner"],
            "x_browser_steps": [{"op": "hover", "selector": "#x"}],
        },
    }
    result = execute_web_action(action, workspace_uri=f"file:{workspace}")
    assert result.exit_code == 1
    assert b"unsupported browser op" in result.stdout
