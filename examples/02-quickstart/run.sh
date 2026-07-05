#!/usr/bin/env bash
# §2 quick start: drive one goal to completion by hand.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Run a no-op and finish." "create_quickstart_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

PREVIEW="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=preview)"
printf '%s' "$PREVIEW" | jq -e '.result.executable == false' >/dev/null

ISSUED="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue)"
REC_ID="$(printf '%s' "$ISSUED" | jq -r '.result.recommendation_id')"
ACT_ID="$(printf '%s' "$ISSUED" | jq -r '.result.action.action_id')"

EVENT="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
  | jq -c 'select(.type == "recommendation.issued")' | tail -1)"
SEQ="$(printf '%s' "$EVENT" | jq -r '.seq')"
HASH="$(printf '%s' "$EVENT" | jq -r '.event_hash')"

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_accept_01",
  "goal_id": "${GOAL_ID}",
  "recommendation_id": "${REC_ID}",
  "action_id": "${ACT_ID}",
  "issued_event_seq": ${SEQ},
  "issued_event_hash": "${HASH}",
  "created_at": "2026-07-05T12:01:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "recommendation_disposition",
  "recommendation_disposition": {"disposition": "accepted"}
}
EOF

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_started_01",
  "goal_id": "${GOAL_ID}",
  "recommendation_id": "${REC_ID}",
  "action_id": "${ACT_ID}",
  "issued_event_seq": ${SEQ},
  "issued_event_hash": "${HASH}",
  "created_at": "2026-07-05T12:02:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "action_started",
  "action_started": {"started_at": "2026-07-05T12:02:00Z"}
}
EOF

true

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_result_01",
  "goal_id": "${GOAL_ID}",
  "recommendation_id": "${REC_ID}",
  "action_id": "${ACT_ID}",
  "issued_event_seq": ${SEQ},
  "issued_event_hash": "${HASH}",
  "created_at": "2026-07-05T12:03:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "action_result",
  "action_result": {
    "status": "completed",
    "changed": "no",
    "started_at": "2026-07-05T12:02:00Z",
    "ended_at": "2026-07-05T12:03:00Z",
    "process": {"exit_code": 0, "signal": null, "timed_out": false},
    "output": {"stdout": "", "stderr": ""}
  }
}
EOF

DONE="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue)"
DONE_REC="$(printf '%s' "$DONE" | jq -r '.result.recommendation_id')"
printf '%s' "$DONE" | jq -e '.result.recommendation_type == "done"' >/dev/null

DONE_EVENT="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 \
  | jq -c 'select(.type == "recommendation.issued")' | tail -1)"
DONE_SEQ="$(printf '%s' "$DONE_EVENT" | jq -r '.seq')"
DONE_HASH="$(printf '%s' "$DONE_EVENT" | jq -r '.event_hash')"

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_accept_done_01",
  "goal_id": "${GOAL_ID}",
  "recommendation_id": "${DONE_REC}",
  "issued_event_seq": ${DONE_SEQ},
  "issued_event_hash": "${DONE_HASH}",
  "created_at": "2026-07-05T12:04:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "recommendation_disposition",
  "recommendation_disposition": {"disposition": "accepted"}
}
EOF

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null
echo "§2 quickstart: goal ${GOAL_ID} succeeded"
