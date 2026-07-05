#!/usr/bin/env bash
# Appendix pocket session: automate, unblock, audit.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${FIXTURES}/pocket_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Pocket session demo." "create_pocket_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

set +e
EXEC_OUT="$(wfe --store "$STORE" --brain-playbook "$PLAYBOOK" run --goal-id "$GOAL_ID" 2>&1)"
STOPPED=$?
set -e
STOP_REASON="$(printf '%s' "$EXEC_OUT" | jq -r '.result.stopped_reason // empty')"
if [[ "$STOP_REASON" != "question" ]]; then
  echo "expected executor to stop on question recommendation (got ${STOP_REASON:-exit ${STOPPED}})" >&2
  exit 1
fi

STATUS="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$STATUS" | jq -e '.result.goal_status == "waiting"' >/dev/null

REC_ID="$(printf '%s' "$STATUS" | jq -r '.result.last_issued_recommendation_id')"

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_pocket_answer",
  "goal_id": "${GOAL_ID}",
  "recommendation_id": "${REC_ID}",
  "created_at": "2026-07-05T12:05:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "question_answer",
  "question_answer": {"question_id": "q_pocket", "answer": "pnpm"}
}
EOF

wfe --store "$STORE" --brain-playbook "$PLAYBOOK" run --goal-id "$GOAL_ID" >/dev/null

FINAL="$(wf --store "$STORE" status --goal-id "$GOAL_ID")"
printf '%s' "$FINAL" | jq -e '.result.goal_status == "succeeded"' >/dev/null

VERIFY="$(wf --store "$STORE" verify --goal-id "$GOAL_ID")"
printf '%s' "$VERIFY" | jq -e '.result.ok == true' >/dev/null

HISTORY_COUNT="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"
if [[ "$HISTORY_COUNT" -lt 5 ]]; then
  echo "expected a full pocket-session history stream" >&2
  exit 1
fi

echo "appendix pocket: goal ${GOAL_ID} succeeded with verify ok"
