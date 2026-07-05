#!/usr/bin/env bash
# §9 machines: preview offline §9 brains (make, bt, plan) when dependencies exist.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
require_scripted "$@"
require_jq

PLAYBOOK="${ROOT}/tests/exec/fixtures/true_playbook.json"
new_workspace
mkdir -p "${WORKSPACE}/project/dist" "${WORKSPACE}/project/src"
printf 'hello\n' >"${WORKSPACE}/project/src/data.txt"
cat >"${WORKSPACE}/project/Makefile" <<'EOF'
dist/report.pdf: src/data.txt
	mkdir -p dist
	cp src/data.txt dist/report.pdf
EOF

preview_make() {
  if ! command -v make >/dev/null 2>&1; then
    echo "skip §9.3 wayfinder-make: make not on PATH"
    return 0
  fi
  local created goal_id preview
  created="$(goal_create "Bring dist/report.pdf up to date." "create_make_01")"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  preview="$(uv run wayfinder-make dist/report.pdf --store "$STORE" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '.result.recommendation_type == "action"' >/dev/null
  echo "§9.3 wayfinder-make: preview ok for ${goal_id}"
}

preview_bt() {
  if ! uv run python3 -c "import py_trees" >/dev/null 2>&1; then
    echo "skip §9.5 wayfinder-bt: py_trees not installed"
    return 0
  fi
  local tree created goal_id preview
  tree="${ROOT}/examples/trees/test-check.bt"
  created="$(goal_create "Keep staging healthy." "create_bt_01")"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  preview="$(uv run wayfinder-bt --tree "$tree" --store "$STORE" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '.result.recommendation_type == "action"' >/dev/null
  echo "§9.5 wayfinder-bt: preview ok for ${goal_id}"
}

preview_plan() {
  if ! uv run python3 -c "import pyperplan" >/dev/null 2>&1; then
    echo "skip §9.2 wayfinder-plan: pyperplan not installed"
    return 0
  fi
  local domain created goal_id preview
  domain="${ROOT}/examples/domains/cluster-maintenance.pddl"
  created="$(wf --store "$STORE" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_plan_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "Upgrade node3 without dropping below two serving nodes.",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "low"},
  "metadata": {
    "pddl_problem": "(define (problem upgrade-node3)\n  (:domain cluster-maintenance)\n  (:objects n3 n4 n5 - node)\n  (:init (serving n3) (serving n4) (serving n5))\n  (:goal (and (upgraded n3) (serving n3)))\n)",
    "plan_actions": {
      "drain n3": {"argv": ["echo", "drain", "n3"], "title": "Drain node3"},
      "upgrade n3": {"argv": ["echo", "upgrade", "n3"], "title": "Upgrade node3"},
      "bring-online n3": {"argv": ["echo", "online", "n3"], "title": "Bring node3 online"}
    }
  }
}
EOF
)"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  preview="$(uv run wayfinder-plan --domain "$domain" --store "$STORE" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '.result.recommendation_type == "action"' >/dev/null
  printf '%s' "$preview" | jq -e '.result.executable == false' >/dev/null
  echo "§9.2 wayfinder-plan: preview ok for ${goal_id}"
}

preview_make
preview_bt
preview_plan
echo "§9 machines: offline previews complete"
