#!/usr/bin/env bash
# §8 prose front-end: wayfinder-tell with a local LLM stub.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
STUB_RUNNER="${ROOT}/examples/scripts/run_with_llm_stub.py"
RESPONSES="${FIXTURES}/tell_observation_responses.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

CREATED="$(goal_create "Record an observation in prose." "create_prose_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"
wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue >/dev/null

BEFORE="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"

uv run python "$STUB_RUNNER" --responses "$RESPONSES" -- \
  uv run wayfinder-tell --store "$STORE" --goal-id "$GOAL_ID" "Switched the workspace to pnpm."

AFTER="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"
if [[ "$AFTER" -le "$BEFORE" ]]; then
  echo "expected wayfinder-tell to append an observation event" >&2
  exit 1
fi

echo "§8 prose: wayfinder-tell recorded an observation for ${GOAL_ID}"
