#!/usr/bin/env bash
# §10 deference example: parent goal delegates to a sub-wayfinder store.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SCRIPTED=0
for arg in "$@"; do
  case "$arg" in
    --scripted) SCRIPTED=1 ;;
  esac
done

if [[ "$SCRIPTED" -ne 1 ]]; then
  echo "This example requires --scripted until a release playbook is configured." >&2
  exit 1
fi

WORKSPACE="$(mktemp -d)"
cleanup() { rm -rf "$WORKSPACE"; }
trap cleanup EXIT

PARENT_STORE="$WORKSPACE/parent-store"
SUB_STORE="$WORKSPACE/sub-store"
SUB_GOAL_FILE="$WORKSPACE/subgoal.txt"
SUB_PLAYBOOK="$ROOT/tests/exec/fixtures/true_playbook.json"
PARENT_PLAYBOOK="$WORKSPACE/parent_playbook.json"

mkdir -p "$WORKSPACE/project"

SUB_CREATED="$(uv run wayfinder --store "$SUB_STORE" --brain-playbook "$SUB_PLAYBOOK" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_sub_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "example", "authority": "owner", "authenticated": true},
  "description": "Generate changelog since v2.3.0",
  "workspace_uri": "file:$WORKSPACE/project",
  "policy": {"max_auto_risk_level": "low"}
}
EOF
)"
SUB_GOAL_ID="$(printf '%s' "$SUB_CREATED" | jq -r '.result.goal.goal_id')"
printf '%s' "$SUB_GOAL_ID" >"$SUB_GOAL_FILE"

WAYFINDER_CMD="$(uv run python3 -c "import shlex, sys; print(shlex.join([sys.executable, '-m', 'wayfinder.cli', '--brain-playbook', sys.argv[1]]))" "$SUB_PLAYBOOK")"
uv run python3 - "$PARENT_PLAYBOOK" "$WORKSPACE/project" "$SUB_STORE" "$SUB_GOAL_FILE" "$WAYFINDER_CMD" <<'PY'
import json
import sys
from pathlib import Path

playbook_path, workspace, sub_store, sub_goal_file, wayfinder_cmd = sys.argv[1:6]
argv = [
    sys.executable,
    "-m",
    "wayfinder.exec",
    "--wayfinder",
    wayfinder_cmd,
    "--store",
    sub_store,
    "run",
    "--goal-file",
    sub_goal_file,
]
payload = {
    "rules": [
        {
            "match": {"goal_status": "pending", "open_recommendation_id": {"$null": True}},
            "recommendation": {
                "recommendation_type": "action",
                "summary": "Delegate changelog generation to a sub-wayfinder.",
                "goal_status": "running",
                "confidence": 0.9,
                "action": {
                    "kind": "shell",
                    "title": "Run sub-goal",
                    "shell": {
                        "argv": argv,
                        "command_for_display": "wayfinder-exec run (sub-goal)",
                        "cwd": f"file:{workspace}",
                        "env": {"mode": "minimal", "set": {}},
                        "stdin": {"mode": "none"},
                        "pty": False,
                        "timeout_seconds": 300,
                        "expected_exit_codes": [0],
                        "requires_shell": False,
                    },
                    "preconditions": [],
                    "success_criteria": [],
                },
                "idempotency": {
                    "level": "strong",
                    "key": "idem_delegate",
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
                    "classes": ["read_local", "execute_local", "write_workspace"],
                    "blast_radius": "workspace",
                    "requires_approval": False,
                    "destructive": False,
                    "network": "not_required",
                    "secrets": "not_required",
                    "rollback": {"available": False, "kind": "unknown", "instructions": None},
                },
            },
        },
        {
            "match": {"completed_steps": {"$gte": 1}, "open_recommendation_id": {"$null": True}},
            "recommendation": {
                "recommendation_type": "done",
                "summary": "Sub-goal finished.",
                "goal_status": "running",
                "confidence": 0.95,
                "done": {"reason": "Delegated sub-goal completed."},
            },
        },
    ],
}
Path(playbook_path).write_text(json.dumps(payload), encoding="utf-8")
PY

PARENT_CREATED="$(uv run wayfinder --store "$PARENT_STORE" --brain-playbook "$PARENT_PLAYBOOK" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_parent_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "example", "authority": "owner", "authenticated": true},
  "description": "Cut and publish release 2.4.0",
  "workspace_uri": "file:$WORKSPACE/project",
  "policy": {"max_auto_risk_level": "low"}
}
EOF
)"
PARENT_GOAL_ID="$(printf '%s' "$PARENT_CREATED" | jq -r '.result.goal.goal_id')"

uv run wayfinder-exec --store "$PARENT_STORE" --brain-playbook "$PARENT_PLAYBOOK" run --goal-id "$PARENT_GOAL_ID"

echo "Parent log:"
uv run wayfinder --store "$PARENT_STORE" history --goal-id "$PARENT_GOAL_ID" --since-seq 0 | jq -c '{seq, type}'

echo "Sub log:"
uv run wayfinder --store "$SUB_STORE" history --goal-id "$SUB_GOAL_ID" --since-seq 0 | jq -c '{seq, type}'
