"""Dependency and credential checks for wayfinder doctor (Phase 9)."""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from typing import Any

from wayfinder import __version__
from wayfinder.llm.config import DEFAULT_CONFIG_PATH, load_llm_config


@dataclass(frozen=True)
class DoctorCheck:
    """One readiness probe surfaced by `wayfinder doctor`."""

    check_id: str
    section: str
    name: str
    status: str
    detail: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        payload: dict[str, str | None] = {
            "id": self.check_id,
            "section": self.section,
            "name": self.name,
            "status": self.status,
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def _binary_status(name: str) -> DoctorCheck:
    path = shutil.which(name)
    if path:
        return DoctorCheck(
            check_id=f"binary.{name}",
            section="core",
            name=name,
            status="ready",
            detail=path,
        )
    return DoctorCheck(
        check_id=f"binary.{name}",
        section="core",
        name=name,
        status="missing",
        detail=f"{name} not found on PATH",
    )


def _module_status(module: str, *, section: str, name: str) -> DoctorCheck:
    if importlib.util.find_spec(module) is not None:
        return DoctorCheck(
            check_id=f"module.{module}",
            section=section,
            name=name,
            status="ready",
        )
    return DoctorCheck(
        check_id=f"module.{module}",
        section=section,
        name=name,
        status="missing",
        detail="install the machines extra: uv sync --extra machines",
    )


def _env_status(env_var: str, *, section: str, name: str) -> DoctorCheck:
    value = os.environ.get(env_var, "").strip()
    if value:
        return DoctorCheck(
            check_id=f"env.{env_var.lower()}",
            section=section,
            name=name,
            status="ready",
        )
    return DoctorCheck(
        check_id=f"env.{env_var.lower()}",
        section=section,
        name=name,
        status="missing",
        detail=f"set {env_var} or configure it in {DEFAULT_CONFIG_PATH}",
    )


def _example_status(
    *,
    check_id: str,
    section: str,
    name: str,
    ready: bool,
    detail: str,
) -> DoctorCheck:
    return DoctorCheck(
        check_id=check_id,
        section=section,
        name=name,
        status="ready" if ready else "missing",
        detail=detail,
    )


def run_doctor() -> dict[str, Any]:
    """Collect readiness checks for core tools, machines, and credentials."""
    checks: list[DoctorCheck] = [
        DoctorCheck(
            check_id="wayfinder.version",
            section="core",
            name="wayfinder",
            status="ready",
            detail=__version__,
        ),
        _binary_status("jq"),
        _binary_status("make"),
        _binary_status("task"),
        _binary_status("ansible-playbook"),
        _binary_status("gh"),
        _binary_status("temporal"),
        _binary_status("ffmpeg"),
        _binary_status("curl"),
        _module_status("py_trees", section="§9.5", name="wayfinder-bt"),
        _module_status("pyperplan", section="§9.2", name="wayfinder-plan"),
        _module_status("pexpect", section="§9.8", name="wayfinder-exec-pty"),
        _module_status("temporalio", section="§9.6", name="wayfinder-exec-temporal"),
        _module_status("playwright", section="§9.10", name="wayfinder-web"),
        _env_status("GITHUB_TOKEN", section="§9.4", name="wayfinder-bridge gh"),
        _env_status("BROWSERBASE_API_KEY", section="§9.10", name="wayfinder-web (Browserbase)"),
    ]

    try:
        config = load_llm_config()
        llm_check = DoctorCheck(
            check_id="llm.endpoint",
            section="§5",
            name="LLM brain",
            status="ready",
            detail=f"{config.model} @ {config.base_url}",
        )
    except Exception as exc:
        llm_check = DoctorCheck(
            check_id="llm.endpoint",
            section="§5",
            name="LLM brain",
            status="missing",
            detail=str(exc),
        )
    checks.append(llm_check)

    temporal_host = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    host, _, port = temporal_host.partition(":")
    port = port or "7233"
    temporal_ready = False
    try:
        import socket

        with socket.create_connection((host, int(port)), timeout=1.0):
            temporal_ready = True
    except OSError:
        temporal_ready = False

    checks.extend(
        [
            _example_status(
                check_id="example.07-wrap",
                section="examples",
                name="§7.1 wrap (ffmpeg)",
                ready=shutil.which("ffmpeg") is not None,
                detail="ffmpeg on PATH" if shutil.which("ffmpeg") else "install ffmpeg for §7.1",
            ),
            _example_status(
                check_id="example.09-bridge-gh",
                section="examples",
                name="§9.4 bridge gh",
                ready=bool(os.environ.get("GITHUB_TOKEN", "").strip())
                and bool(os.environ.get("WAYFINDER_BRIDGE_REPO", "").strip()),
                detail="set GITHUB_TOKEN and WAYFINDER_BRIDGE_REPO for live bridge example",
            ),
            _example_status(
                check_id="example.09-temporal",
                section="examples",
                name="§9.6 exec-temporal",
                ready=temporal_ready,
                detail=f"start Temporal at {temporal_host} (temporal server start-dev)",
            ),
            _example_status(
                check_id="example.09-web",
                section="examples",
                name="§9.10 wayfinder-web",
                ready=importlib.util.find_spec("playwright") is not None,
                detail=(
                    "playwright installed"
                    if importlib.util.find_spec("playwright") is not None
                    else "uv sync --extra machines && uv run playwright install chromium"
                ),
            ),
        ],
    )

    required = {
        check.check_id
        for check in checks
        if check.section == "core" and check.check_id != "wayfinder.version"
    }
    optional_missing = [
        check for check in checks if check.status == "missing" and check.check_id not in required
    ]
    ready = [check for check in checks if check.status == "ready"]

    return {
        "schema": "wip.doctor_result/0.1",
        "protocol_version": "0.1",
        "ready_count": len(ready),
        "missing_count": len(optional_missing),
        "checks": [check.to_dict() for check in checks],
        "summary": " · ".join(
            f"{check.name}: {check.status}"
            for check in checks
            if check.section in {"core", "§5", "§9.4"}
        ),
    }
