#!/usr/bin/env bash
# §9.6 Temporal: durable executor loop (live-only; needs a Temporal dev server).
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
skip_if_scripted "§9.6 wayfinder-exec-temporal (live-only)" "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
TEMPORAL_HOST="${TEMPORAL_ADDRESS%%:*}"
TEMPORAL_PORT="${TEMPORAL_ADDRESS##*:}"
if [[ "$TEMPORAL_HOST" == "$TEMPORAL_PORT" ]]; then
  TEMPORAL_HOST="localhost"
  TEMPORAL_PORT="7233"
fi

if ! tcp_reachable "$TEMPORAL_HOST" "$TEMPORAL_PORT"; then
  echo "Temporal server not reachable at ${TEMPORAL_HOST}:${TEMPORAL_PORT}." >&2
  echo "Start one with: temporal server start-dev" >&2
  exit 1
fi

new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Run via durable Temporal workflow." "create_temporal_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

uv run wayfinder-exec-temporal \
  --store "$STORE" \
  --brain-playbook "$PLAYBOOK" \
  --temporal-address "${TEMPORAL_ADDRESS:-localhost:7233}" \
  run --goal-id "$GOAL_ID" >/dev/null

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null
echo "§9.6 temporal: goal ${GOAL_ID} succeeded via wayfinder-exec-temporal"
