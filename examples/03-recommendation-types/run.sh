#!/usr/bin/env bash
# §3 recommendation types: preview each of the six non-parallel types.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${FIXTURES}/six_types_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

issue_type() {
  local tag="$1"
  local expected="$2"
  local created
  created="$(goal_create "type:${tag}" "create_type_${tag}")"
  local goal_id
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  local issued
  issued="$(wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$goal_id" --mode=issue)"
  local actual
  actual="$(printf '%s' "$issued" | jq -r '.result.recommendation_type')"
  if [[ "$actual" != "$expected" ]]; then
    echo "expected recommendation_type ${expected} for type:${tag}, got ${actual}" >&2
    exit 1
  fi
  echo "type:${tag} -> ${actual}"
}

issue_type question question
issue_type wait wait
issue_type blocked blocked
issue_type unsafe unsafe
issue_type done done
issue_type action action

echo "§3 recommendation types: all six types issued successfully"
