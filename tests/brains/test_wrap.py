"""Wrap brain risk inference tests."""

from __future__ import annotations

from wayfinder.brains.wrap import enforce_wrap_risk


def _curl_action(argv: list[str]) -> dict[str, object]:
    return {
        "recommendation_type": "action",
        "summary": "Fetch remote resource.",
        "goal_status": "running",
        "confidence": 0.8,
        "action": {
            "kind": "shell",
            "title": "curl",
            "shell": {
                "argv": argv,
                "command_for_display": " ".join(argv),
                "cwd": "file:/tmp",
                "env": {"mode": "minimal", "set": {}},
                "stdin": {"mode": "none"},
                "pty": False,
                "timeout_seconds": 30,
                "expected_exit_codes": [0],
                "requires_shell": False,
            },
        },
        "idempotency": {
            "level": "strong",
            "key": "idem_curl",
            "scope": "workspace",
            "safe_to_retry": True,
            "safe_to_run_if_already_done": False,
            "detects_noop": False,
            "dedupe_strategy": "idempotency_key",
            "partial_failure_recovery": "retry",
            "max_attempts": 1,
        },
        "risk": {
            "level": "low",
            "classes": ["execute_local"],
            "blast_radius": "workspace",
            "requires_approval": False,
            "destructive": False,
            "network": "not_required",
            "secrets": "not_required",
            "rollback": {"available": False, "kind": "unknown", "instructions": None},
        },
    }


def test_enforce_wrap_risk_adds_network_read_for_curl_get() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(["curl", "-fsS", "https://example.com/status"]),
        tool="curl",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "network_read" in risk["classes"]
    assert risk["network"] == "required"
    assert risk["requires_approval"] is True


def test_enforce_wrap_risk_adds_network_write_for_curl_post() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(["curl", "-X", "POST", "https://example.com/items"]),
        tool="curl",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "network_write" in risk["classes"]
    assert risk["requires_approval"] is True


def test_enforce_wrap_risk_adds_network_read_for_gh_status() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(["gh", "api", "repos/octocat/Hello-World"]),
        tool="gh",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "network_read" in risk["classes"]
    assert risk["network"] == "required"


def test_enforce_wrap_risk_leaves_ffmpeg_untouched() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(["ffmpeg", "-i", "in.mov", "out.mp4"]),
        tool="ffmpeg",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert risk["network"] == "not_required"


def test_enforce_wrap_risk_ansible_check_is_network_read() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(
            [
                "ansible-playbook",
                "--check",
                "--diff",
                "-i",
                "inventory/prod.ini",
                "site.yml",
            ],
        ),
        tool="ansible",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "network_read" in risk["classes"]
    assert "network_write" not in risk["classes"]
    idempotency = recommendation["idempotency"]
    assert isinstance(idempotency, dict)
    assert idempotency["safe_to_run_if_already_done"] is True


def test_enforce_wrap_risk_ansible_apply_is_network_write() -> None:
    recommendation = enforce_wrap_risk(
        _curl_action(
            [
                "ansible-playbook",
                "-i",
                "inventory/prod.ini",
                "site.yml",
            ],
        ),
        tool="ansible",
    )
    risk = recommendation["risk"]
    assert isinstance(risk, dict)
    assert "network_write" in risk["classes"]
