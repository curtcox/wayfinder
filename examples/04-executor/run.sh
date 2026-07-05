#!/usr/bin/env bash
# §4 executor loop: dry-run then run to completion.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Let the executor drive." "create_executor_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

DRY="$(wfe --store "$STORE" --brain-playbook "$PLAYBOOK" dry-run --goal-id "$GOAL_ID")"
printf '%s' "$DRY" | jq -e '.result.recommendation.recommendation_type == "action"' >/dev/null

wfe --store "$STORE" --brain-playbook "$PLAYBOOK" run --goal-id "$GOAL_ID" >/dev/null

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null
echo "§4 executor: goal ${GOAL_ID} succeeded via wayfinder-exec"
