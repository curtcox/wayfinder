#!/usr/bin/env bash
# §5 audit commands: status, history, explain, verify after one executor run.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Audit this goal." "create_audit_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

wfe --store "$STORE" --brain-playbook "$PLAYBOOK" run --goal-id "$GOAL_ID" >/dev/null

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null

HISTORY_COUNT="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"
if [[ "$HISTORY_COUNT" -lt 4 ]]; then
  echo "expected a rich history, got ${HISTORY_COUNT} events" >&2
  exit 1
fi

REC_ID="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
  | jq -r 'select(.type == "recommendation.issued") | .data.recommendation.recommendation_id' | head -1)"
EXPLAIN="$(wf --store "$STORE" explain --goal-id "$GOAL_ID" --recommendation-id "$REC_ID")"
printf '%s' "$EXPLAIN" | jq -e '.result.explanation.summary != null' >/dev/null

VERIFY="$(wf --store "$STORE" verify --goal-id "$GOAL_ID")"
printf '%s' "$VERIFY" | jq -e '.result.ok == true' >/dev/null

echo "§5 audit: status/history/explain/verify ok for ${GOAL_ID}"
