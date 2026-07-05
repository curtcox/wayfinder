#!/usr/bin/env bash
# §12 errors: stable error.code values for common failure modes.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

# invalid_input: next on a terminal goal after cancellation.
CREATED="$(goal_create "Demonstrate invalid_input." "create_errors_cancel")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

wf --store "$STORE" update --goal-id "$GOAL_ID" <<EOF
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_cancel_errors",
  "goal_id": "${GOAL_ID}",
  "created_at": "2026-07-05T12:09:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "update_type": "goal_cancel",
  "goal_cancel": {"reason": "done demonstrating errors"}
}
EOF

NEXT="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue || true)"
printf '%s' "$NEXT" | jq -e '.error.code == "invalid_input"' >/dev/null

# policy_denied: operator cannot cancel a goal.
CREATED="$(goal_create "Demonstrate policy_denied." "create_errors_policy")"
GOAL_P="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"

DENIED="$(wf --store "$STORE" update --goal-id "$GOAL_P" <<EOF || true
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_denied_cancel",
  "goal_id": "${GOAL_P}",
  "created_at": "2026-07-05T12:10:00Z",
  "actor": {"type": "human", "id": "other", "authority": "operator", "authenticated": true},
  "update_type": "goal_cancel",
  "goal_cancel": {"reason": "not allowed"}
}
EOF
)"
printf '%s' "$DENIED" | jq -e '.error.code == "policy_denied"' >/dev/null

# invalid_input: conflicting create_id replay with different body.
BODY_A='{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_conflict_01",
  "created_at": "2026-07-05T12:11:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "First body.",
  "workspace_uri": "file:'"${WORKSPACE}"'/project",
  "policy": {"max_auto_risk_level": "low"}
}'
BODY_B='{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_conflict_01",
  "created_at": "2026-07-05T12:11:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "Different body.",
  "workspace_uri": "file:'"${WORKSPACE}"'/project",
  "policy": {"max_auto_risk_level": "low"}
}'

printf '%s' "$BODY_A" | wf --store "$STORE" --brain-playbook "$PLAYBOOK" goal create >/dev/null
CONFLICT="$(printf '%s' "$BODY_B" | wf --store "$STORE" --brain-playbook "$PLAYBOOK" goal create || true)"
printf '%s' "$CONFLICT" | jq -e '.error.code == "invalid_input"' >/dev/null

echo "§12 errors: invalid_input and policy_denied codes verified"
