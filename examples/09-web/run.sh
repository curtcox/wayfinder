#!/usr/bin/env bash
# §9.10 Browser automation: wayfinder-web + wayfinder-exec-web (live-only).
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
skip_if_scripted "§9.10 wayfinder-web (live-only)" "$@"
require_jq

if ! uv run python3 -c "import playwright" >/dev/null 2>&1; then
  echo "playwright not installed; run: uv sync --extra machines && uv run playwright install chromium" >&2
  exit 1
fi

POLICY="${FIXTURES}/web_policy.json"
WAYFINDER_CMD="$(uv run python3 -c "import shlex; print(shlex.join(['uv', 'run', 'wayfinder-web']))")"

new_workspace
mkdir -p "${WORKSPACE}/project/invoices"

CREATED="$(wf --store "$STORE" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_web_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "Download June invoice PDF into ./invoices.",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "medium"},
  "metadata": {
    "web_steps": [
      {
        "title": "Download invoice",
        "steps": [{"op": "await_download", "filename": "june.pdf"}],
        "risk_classes": ["network_read"]
      }
    ]
  }
}
EOF
)"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

uv run wayfinder-exec-web \
  --store "$STORE" \
  --wayfinder "$WAYFINDER_CMD" \
  --policy "$POLICY" \
  run --goal-id "$GOAL_ID" >/dev/null

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null

if [[ -f "${WORKSPACE}/project/invoices/june.pdf" ]]; then
  echo "§9.10 web: goal ${GOAL_ID} downloaded june.pdf via wayfinder-exec-web"
else
  COMPLETED="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
    | jq -c 'select(.type == "action.completed")' | tail -1)"
  printf '%s' "$COMPLETED" | jq -e '.data.action_result.status == "completed"' >/dev/null
  echo "§9.10 web: goal ${GOAL_ID} completed browser action via wayfinder-exec-web"
fi
