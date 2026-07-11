#!/usr/bin/env bash
# §7 wrapping tools: ffmpeg (§7.1) and curl network risk (§7.2) via wayfinder-exec.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

preview_curl_wrap() {
  local curl_playbook created goal_id preview wayfinder_cmd exec_out stop_reason
  curl_playbook="${FIXTURES}/wrap_curl_playbook.json"
  created="$(goal_create "Fetch https://api.example.com/v1/status into status.json." "create_wrap_curl")"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"

  preview="$(wf --store "$STORE" --brain-playbook "$curl_playbook" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '(.result.risk.classes // []) | index("network_read")' >/dev/null
  printf '%s' "$preview" | jq -e '.result.risk.requires_approval == true' >/dev/null

  wayfinder_cmd="$(wayfinder_with_playbook_cmd "$curl_playbook")"
  set +e
  exec_out="$(wfe --store "$STORE" --wayfinder "$wayfinder_cmd" run --goal-id "$goal_id" 2>&1)"
  set -e
  stop_reason="$(printf '%s' "$exec_out" | jq -r '.result.stopped_reason // empty')"
  if [[ "$stop_reason" != "needs_approval" && "$stop_reason" != "policy_denied" ]]; then
    echo "expected executor to stop on network curl action (got ${stop_reason:-none})" >&2
    printf '%s\n' "$exec_out" >&2
    exit 1
  fi
  echo "§7.2 curl wrap: network_read recommendation blocked for ${goal_id}"
}

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

preview_curl_wrap

echo "§7 wrap: goal ${GOAL_ID} succeeded via wayfinder-exec --wayfinder"
