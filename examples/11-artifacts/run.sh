#!/usr/bin/env bash
# §11 artifacts: large stdout spills to a content-addressed artifact.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${FIXTURES}/large_output_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Capture large command output." "create_artifacts_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

wfe --store "$STORE" --brain-playbook "$PLAYBOOK" run --goal-id "$GOAL_ID" >/dev/null

INLINE_LIMIT="$(wf capabilities | jq -r '.result.limits.max_inline_output_bytes')"
COMPLETED="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
  | jq -c 'select(.type == "action.completed")' | tail -1)"
printf '%s' "$COMPLETED" | jq -e '.data.action_result.output.stdout_artifact != null' >/dev/null

ARTIFACT_REF="$(printf '%s' "$COMPLETED" | jq -r '.data.action_result.artifacts[0].uri')"
ARTIFACT_BYTES="$(printf '%s' "$COMPLETED" | jq -r '.data.action_result.artifacts[0].bytes')"
if [[ "$ARTIFACT_BYTES" -le "$INLINE_LIMIT" ]]; then
  echo "expected artifact bytes (${ARTIFACT_BYTES}) to exceed inline limit (${INLINE_LIMIT})" >&2
  exit 1
fi

VERIFY="$(wf --store "$STORE" verify --goal-id "$GOAL_ID")"
printf '%s' "$VERIFY" | jq -e '.result.ok == true' >/dev/null

echo "§11 artifacts: stdout spilled (${ARTIFACT_BYTES} bytes) to ${ARTIFACT_REF}"
