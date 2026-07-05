#!/usr/bin/env bash
# §6 talking back: question_answer, approval, observation, and goal_cancel updates.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

new_workspace
mkdir -p "${WORKSPACE}/project"

# 6.1 Answer a question recommendation.
PLAYBOOK="${FIXTURES}/six_types_playbook.json"
CREATED="$(goal_create "type:question" "create_updates_question")"
GOAL_Q="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"
ISSUED_Q="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_Q" --mode=issue)"
REC_Q="$(printf '%s' "$ISSUED_Q" | jq -r '.result.recommendation_id')"
printf '%s' "$ISSUED_Q" | jq -e '.result.recommendation_type == "question"' >/dev/null

wf --store "$STORE" update --goal-id "$GOAL_Q" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_answer_01",
  "goal_id": "${GOAL_Q}",
  "recommendation_id": "${REC_Q}",
  "created_at": "2026-07-05T12:05:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "question_answer",
  "question_answer": {"question_id": "q_example", "answer": "pnpm"}
}
EOF

# 6.2 Grant approval for a recommendation that requires it.
PLAYBOOK="${FIXTURES}/approval_playbook.json"
CREATED="$(goal_create "Run an approved no-op." "create_updates_approval")"
GOAL_A="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"
wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_A" --mode=issue >/dev/null
EVENT_A="$(last_issued_event "$GOAL_A")"
REC_A="$(printf '%s' "$EVENT_A" | jq -r '.data.recommendation.recommendation_id')"
ACT_A="$(printf '%s' "$EVENT_A" | jq -r '.data.recommendation.action.action_id')"
SEQ_A="$(printf '%s' "$EVENT_A" | jq -r '.seq')"
HASH_A="$(printf '%s' "$EVENT_A" | jq -r '.event_hash')"

wf --store "$STORE" update --goal-id "$GOAL_A" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_approve_01",
  "goal_id": "${GOAL_A}",
  "recommendation_id": "${REC_A}",
  "action_id": "${ACT_A}",
  "issued_event_seq": ${SEQ_A},
  "issued_event_hash": "${HASH_A}",
  "created_at": "2026-07-05T12:06:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "approval",
  "approval": {"decision": "granted", "reason": "Reviewed argv; no-op is safe."}
}
EOF

APPROVAL_COUNT="$(wf --store "$STORE" history --goal-id "$GOAL_A" --since-seq 0 \
  | jq -c 'select(.type == "approval.granted")' | wc -l | tr -d ' ')"
if [[ "$APPROVAL_COUNT" -lt 1 ]]; then
  echo "expected approval.granted event" >&2
  exit 1
fi

# 6.3 Record an observation.
PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
CREATED="$(goal_create "Record a workspace observation." "create_updates_observation")"
GOAL_O="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"
wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_O" --mode=issue >/dev/null

wf --store "$STORE" update --goal-id "$GOAL_O" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_obs_01",
  "goal_id": "${GOAL_O}",
  "created_at": "2026-07-05T12:07:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "observation",
  "observations": [
    {
      "kind": "fact",
      "subject": "workspace.package_manager",
      "predicate": "equals",
      "object": "pnpm",
      "confidence": 1.0,
      "source": "human"
    }
  ]
}
EOF

OBS_COUNT="$(wf --store "$STORE" history --goal-id "$GOAL_O" --since-seq 0 \
  | jq -c 'select(.type == "observation.recorded")' | wc -l | tr -d ' ')"
if [[ "$OBS_COUNT" -lt 1 ]]; then
  echo "expected observation.recorded event" >&2
  exit 1
fi

# 6.6 Cancel a goal; next on a terminal goal returns invalid_input.
CREATED="$(goal_create "Cancel this goal." "create_updates_cancel")"
GOAL_C="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

wf --store "$STORE" update --goal-id "$GOAL_C" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_cancel_01",
  "goal_id": "${GOAL_C}",
  "created_at": "2026-07-05T12:08:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "goal_cancel",
  "goal_cancel": {"reason": "Requirements changed; abandoning this approach."}
}
EOF

NEXT_C="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_C" --mode=issue || true)"
printf '%s' "$NEXT_C" | jq -e '.error.code == "invalid_input"' >/dev/null

echo "§6 updates: question_answer, approval, observation, and goal_cancel ok"
