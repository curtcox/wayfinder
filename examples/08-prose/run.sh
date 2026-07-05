#!/usr/bin/env bash
# §8 prose front-ends: wayfinder-tell, ask, do, and chat with a local LLM stub.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
QUESTION_PLAYBOOK="${FIXTURES}/six_types_playbook.json"
STUB_RUNNER="${ROOT}/examples/scripts/run_with_llm_stub.py"
TELL_RESPONSES="${FIXTURES}/tell_observation_responses.json"
new_workspace
mkdir -p "${WORKSPACE}/project"

ASK_RESPONSES="${WORKSPACE}/ask_responses.json"
CHAT_RESPONSES="${WORKSPACE}/chat_responses.json"
printf '%s\n' '["The goal is running with an open action recommendation [seq 3]."]' >"$ASK_RESPONSES"
printf '%s\n' '["Status is waiting for input after a question [seq 3]."]' >"$CHAT_RESPONSES"

# 8.2 wayfinder-tell records an observation in history.
CREATED="$(goal_create "Record an observation in prose." "create_prose_01")"
GOAL_ID="$(printf '%s' "$CREATED" | jq -r '.result.goal.goal_id')"
wf --store "$STORE" --brain-playbook "$PLAYBOOK" next --goal-id "$GOAL_ID" --mode=issue >/dev/null

BEFORE="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"

uv run python "$STUB_RUNNER" --responses "$TELL_RESPONSES" -- \
  uv run wayfinder-tell --store "$STORE" --goal-id "$GOAL_ID" "Switched the workspace to pnpm."

AFTER="$(wf --store "$STORE" history --goal-id "$GOAL_ID" --since-seq 0 | wc -l | tr -d ' ')"
if [[ "$AFTER" -le "$BEFORE" ]]; then
  echo "expected wayfinder-tell to append an observation event" >&2
  exit 1
fi

# 8.3 wayfinder-ask synthesizes an answer from status/history.
ASK_OUT="$(uv run python "$STUB_RUNNER" --responses "$ASK_RESPONSES" -- \
  uv run wayfinder-ask --store "$STORE" --goal-id "$GOAL_ID" "what is blocking progress?")"
if ! printf '%s' "$ASK_OUT" | grep -Eqi 'running|recommendation|seq'; then
  echo "expected wayfinder-ask to synthesize a status answer" >&2
  exit 1
fi

# 8.1 wayfinder-do creates a goal and drives the executor loop.
DO_STORE="${WORKSPACE}/do-store"
mkdir -p "${WORKSPACE}/do-project"
DO_RESPONSES="${WORKSPACE}/do_responses.json"
printf '[%s]\n' "$(jq -nc \
  --arg desc "Run a no-op command." \
  --arg path "${WORKSPACE}/do-project" \
  '{description: $desc, workspace_path: $path, max_auto_risk_level: "low"}')" \
  >"$DO_RESPONSES"

WAYFINDER_CMD="$(wayfinder_with_playbook_cmd "$PLAYBOOK")"
DO_RESULT="$(uv run python "$STUB_RUNNER" --responses "$DO_RESPONSES" -- \
  uv run wayfinder-do --format json --store "$DO_STORE" --wayfinder "$WAYFINDER_CMD" \
  "Run a no-op command in this project.")"
DO_GOAL_ID="$(printf '%s' "$DO_RESULT" | jq -r '.result.goal_id')"
DO_STATUS="$(wf --store "$DO_STORE" status --goal-id "$DO_GOAL_ID")"
printf '%s' "$DO_STATUS" | jq -e '.result.goal_status == "succeeded"' >/dev/null

# 8.5 wayfinder-chat handles one ask turn from stdin.
QUESTION_GOAL="$(goal_create "type:question" "create_prose_chat")"
CHAT_GOAL_ID="$(printf '%s' "$QUESTION_GOAL" | jq -r '.result.goal.goal_id')"
wf --store "$STORE" --brain-playbook "$QUESTION_PLAYBOOK" next --goal-id "$CHAT_GOAL_ID" --mode=issue >/dev/null

CHAT_OUT="$(printf 'what is the status?\n' | uv run python "$STUB_RUNNER" --responses "$CHAT_RESPONSES" -- \
  uv run wayfinder-chat --store "$STORE" --goal-id "$CHAT_GOAL_ID")"
if ! printf '%s' "$CHAT_OUT" | grep -qi chat; then
  echo "expected wayfinder-chat banner in output" >&2
  exit 1
fi

echo "§8 prose: tell, ask, do, and chat exercised for ${GOAL_ID}"
