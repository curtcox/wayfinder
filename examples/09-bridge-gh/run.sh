#!/usr/bin/env bash
# §9.4 GitHub bridge: mirror goal events onto a GitHub issue (live-only).
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
skip_if_scripted "§9.4 wayfinder-bridge gh (live-only)" "$@"
require_jq

require_live_env GITHUB_TOKEN
require_live_env WAYFINDER_BRIDGE_REPO

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Rotate staging TLS certificates." "create_bridge_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

SYNC1="$(uv run wayfinder-bridge gh sync \
  --store "$STORE" \
  --goal-id "$GOAL_ID" \
  --repo "$WAYFINDER_BRIDGE_REPO")"
ISSUE="$(printf '%s' "$SYNC1" | jq -r '.result.issue_number')"
printf '%s' "$SYNC1" | jq -e '.result.events_synced >= 1' >/dev/null

wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue >/dev/null

SYNC2="$(uv run wayfinder-bridge gh sync \
  --store "$STORE" \
  --goal-id "$GOAL_ID" \
  --repo "$WAYFINDER_BRIDGE_REPO")"
printf '%s' "$SYNC2" | jq -e '.result.events_synced >= 1' >/dev/null

echo "§9.4 bridge gh: goal ${GOAL_ID} mirrored to issue #${ISSUE} in ${WAYFINDER_BRIDGE_REPO}"
