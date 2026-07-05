#!/usr/bin/env bash
# §7 wrapping tools: drive a tool-specialized wayfinder via wayfinder-exec.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${FIXTURES}/wrap_ffmpeg_playbook.json"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found; using true-playbook wrap simulation" >&2
  PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
fi

new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Transcode a generated clip to output.mp4." "create_wrap_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

WAYFINDER_CMD="$(wayfinder_with_playbook_cmd "$PLAYBOOK")"
wfe --store "$STORE" --wayfinder "$WAYFINDER_CMD" run --goal-id "$GOAL_ID" >/dev/null

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null

if command -v ffmpeg >/dev/null 2>&1; then
  test -f "${WORKSPACE}/project/output.mp4"
fi

ACTION_COUNT="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
  | jq -c 'select(.type == "action.completed")' | wc -l | tr -d ' ')"
if [[ "$ACTION_COUNT" -lt 1 ]]; then
  echo "expected at least one completed action in wrapped goal history" >&2
  exit 1
fi

echo "§7 wrap: goal ${GOAL_ID} succeeded via wayfinder-exec --wayfinder"
