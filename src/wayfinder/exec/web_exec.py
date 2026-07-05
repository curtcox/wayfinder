"""Browser step execution for wayfinder-exec-web (§9.10 extension)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder.cli.store_paths import parse_workspace_uri
from wayfinder.core.errors import InvalidInputError
from wayfinder.exec.secrets import resolve_secret_ref
from wayfinder.exec.shell_exec import CommandResult, redact_text


@dataclass(frozen=True)
class _WebOutcome:
    exit_code: int
    transcript: str
    download_paths: list[Path]
    redaction_values: list[str]


def _browser_steps(shell: dict[str, Any]) -> list[dict[str, Any]]:
    steps = shell.get("x_browser_steps")
    if not isinstance(steps, list) or not steps:
        msg = "web actions require shell.x_browser_steps"
        raise InvalidInputError(msg)
    parsed: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            msg = f"x_browser_steps[{index}] must include op"
            raise InvalidInputError(msg)
        parsed.append(step)
    return parsed


def _resolve_workspace(workspace_uri: str) -> Path:
    return parse_workspace_uri(workspace_uri)


def _resolve_download_path(workspace: Path, step: dict[str, Any]) -> Path:
    filename = step.get("filename") or step.get("path") or "download.bin"
    name = Path(str(filename)).name
    dest = workspace / name
    if not str(dest.resolve()).startswith(str(workspace.resolve())):
        msg = f"download path escapes workspace: {name}"
        raise InvalidInputError(msg)
    return dest


def _step_value(
    step: dict[str, Any],
    *,
    secrets_path: Path | None,
    redaction_values: list[str],
) -> str:
    if "secret_ref" in step:
        value = resolve_secret_ref(str(step["secret_ref"]), secrets_path=secrets_path)
        redaction_values.append(value)
        return value
    return str(step.get("value", ""))


def _require_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        msg = (
            "playwright is required for live browser execution; "
            "install with the machines extra or use scripted stub steps"
        )
        raise InvalidInputError(msg) from exc
    return sync_playwright


def _browserbase_cdp_url() -> str | None:
    api_key = os.environ.get("BROWSERBASE_API_KEY", "").strip()
    if not api_key:
        return None
    project_id = os.environ.get("BROWSERBASE_PROJECT_ID", "").strip()
    if not project_id:
        return None
    try:
        import httpx
    except ImportError:
        return None
    response = httpx.post(
        "https://www.browserbase.com/v1/sessions",
        headers={"X-BB-API-Key": api_key, "Content-Type": "application/json"},
        json={"projectId": project_id},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    connect_url = payload.get("connectUrl")
    return str(connect_url) if isinstance(connect_url, str) else None


def _run_steps_playwright(
    steps: list[dict[str, Any]],
    *,
    workspace: Path,
    secrets_path: Path | None,
) -> _WebOutcome:
    sync_playwright = _require_playwright()
    transcript_parts: list[str] = []
    redaction_values: list[str] = []
    download_paths: list[Path] = []
    exit_code = 0

    with sync_playwright() as playwright:
        cdp_url = _browserbase_cdp_url()
        if cdp_url:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            transcript_parts.append("backend=browserbase")
        else:
            browser = playwright.chromium.launch(headless=True)
            transcript_parts.append("backend=local_playwright")
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            for index, step in enumerate(steps):
                op = str(step.get("op", ""))
                transcript_parts.append(f"step[{index}] op={op}")
                if op == "navigate":
                    page.goto(str(step.get("url", "")), wait_until="domcontentloaded")
                elif op == "fill":
                    value = _step_value(
                        step,
                        secrets_path=secrets_path,
                        redaction_values=redaction_values,
                    )
                    page.fill(str(step.get("selector", "")), value)
                elif op in {"click", "submit"}:
                    page.click(str(step.get("selector", "")))
                elif op == "await_download":
                    timeout_ms = int(step.get("timeout_ms", 30000))
                    with page.expect_download(timeout=timeout_ms) as download_info:
                        selector = step.get("selector")
                        if selector:
                            page.click(str(selector))
                    download = download_info.value
                    dest = _resolve_download_path(workspace, step)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    download.save_as(dest)
                    download_paths.append(dest)
                    transcript_parts.append(f"downloaded={dest.name}")
                elif op == "screenshot":
                    dest = workspace / str(step.get("filename", "screenshot.png"))
                    page.screenshot(path=str(dest))
                    download_paths.append(dest)
                    transcript_parts.append(f"screenshot={dest.name}")
                else:
                    msg = f"unsupported browser op: {op}"
                    raise InvalidInputError(msg)
        except Exception as exc:
            exit_code = 1
            transcript_parts.append(f"error={exc}")
        finally:
            context.close()
            browser.close()

    transcript = redact_text("\n".join(transcript_parts))
    return _WebOutcome(
        exit_code=exit_code,
        transcript=transcript,
        download_paths=download_paths,
        redaction_values=redaction_values,
    )


def _run_steps_stub(
    steps: list[dict[str, Any]],
    *,
    workspace: Path,
) -> _WebOutcome:
    """Deterministic offline execution for CI when Playwright is unavailable."""
    transcript_parts = ["backend=stub"]
    download_paths: list[Path] = []
    for index, step in enumerate(steps):
        op = str(step.get("op", ""))
        transcript_parts.append(f"step[{index}] op={op}")
        if op == "await_download":
            dest = _resolve_download_path(workspace, step)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"stub-download\n")
            download_paths.append(dest)
            transcript_parts.append(f"downloaded={dest.name}")
        elif op == "screenshot":
            dest = workspace / str(step.get("filename", "screenshot.png"))
            dest.write_bytes(b"stub-screenshot\n")
            download_paths.append(dest)
    return _WebOutcome(
        exit_code=0,
        transcript="\n".join(transcript_parts),
        download_paths=download_paths,
        redaction_values=[],
    )


def execute_web_action(
    action: dict[str, Any],
    *,
    workspace_uri: str,
    secrets_path: Path | None = None,
    force_stub: bool = False,
) -> CommandResult:
    """Drive a shell action through its x_browser_steps table."""
    shell = action.get("shell")
    if not isinstance(shell, dict):
        msg = "shell action missing shell object"
        raise ValueError(msg)
    steps = _browser_steps(shell)
    workspace = _resolve_workspace(workspace_uri)
    started_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    use_stub = force_stub or os.environ.get("WAYFINDER_WEB_STUB", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if use_stub:
        outcome = _run_steps_stub(steps, workspace=workspace)
    else:
        try:
            outcome = _run_steps_playwright(
                steps,
                workspace=workspace,
                secrets_path=secrets_path,
            )
        except InvalidInputError:
            if force_stub:
                raise
            outcome = _run_steps_stub(steps, workspace=workspace)
    ended_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return CommandResult(
        exit_code=outcome.exit_code,
        signal=None,
        timed_out=False,
        stdout=outcome.transcript.encode("utf-8"),
        stderr=b"",
        started_at=started_at,
        ended_at=ended_at,
    )


def build_web_action_result(
    command_result: CommandResult,
    *,
    action: dict[str, Any],
    artifact_store: Any,
    inline_limit: int,
    workspace_uri: str,
) -> dict[str, Any]:
    """Map a browser command result to wip.action_result with transcript + download artifacts."""
    from wayfinder.core.artifacts import ArtifactStore
    from wayfinder.exec.shell_exec import build_action_result

    result = build_action_result(
        command_result,
        action=action,
        artifact_store=artifact_store,
        inline_limit=0,
    )
    output = result.setdefault("output", {})
    transcript = command_result.stdout.decode("utf-8", errors="replace")
    if artifact_store is not None and transcript and isinstance(artifact_store, ArtifactStore):
        ref = artifact_store.write_bytes(
            transcript.encode("utf-8"),
            artifact_id="art_browser_transcript",
            media_type="text/plain",
            description="browser session transcript (redacted)",
        )
        artifact_store.verify_reference(ref)
        result.setdefault("artifacts", []).append(ref)
        output["browser_transcript_artifact"] = ref["artifact_id"]
        output.pop("stdout", None)
        output.pop("stdout_artifact", None)
    elif transcript:
        if len(transcript.encode("utf-8")) <= inline_limit:
            output["browser_transcript"] = transcript
        else:
            output["browser_transcript"] = transcript[:inline_limit]

    shell = action.get("shell", {})
    steps = shell.get("x_browser_steps", []) if isinstance(shell, dict) else []
    workspace = _resolve_workspace(workspace_uri)
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("op")) != "await_download":
                continue
            dest = _resolve_download_path(workspace, step)
            if not dest.is_file():
                continue
            if artifact_store is not None and isinstance(artifact_store, ArtifactStore):
                ref = artifact_store.write_bytes(
                    dest.read_bytes(),
                    artifact_id=f"art_download_{dest.name}",
                    media_type="application/octet-stream",
                    description=f"browser download: {dest.name}",
                )
                artifact_store.verify_reference(ref)
                result.setdefault("artifacts", []).append(ref)
                output.setdefault("downloads", []).append(ref["artifact_id"])
    return result
