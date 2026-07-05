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

preview_tw() {
  if ! command -v task >/dev/null 2>&1; then
    echo "skip §9.1 wayfinder-tw: task not on PATH"
    return 0
  fi
  local created goal_id preview
  created="$(wf --store "$STORE" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_tw_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "Compliance evidence.",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "low"},
  "metadata": {
    "tw_tasks": [
      {"description": "export access logs", "argv": ["echo", "export"]},
      {"description": "run audit script", "depends_on": [0], "argv": ["echo", "audit"], "priority": "H"}
    ]
  }
}
EOF
)"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  preview="$(uv run wayfinder-tw --store "$STORE" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '.result.recommendation_type == "action"' >/dev/null
  echo "§9.1 wayfinder-tw: preview ok for ${goal_id}"
}

preview_codex() {
  local created goal_id preview
  created="$(wf --store "$STORE" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_codex_01",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "Find and fix the memory leak the soak test keeps hitting.",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "low"},
  "metadata": {
    "codex_steps": [
      {"argv": ["grep", "-r", "malloc", "."], "title": "Search for malloc usage"},
      {"argv": ["true"], "title": "Verify fix"}
    ]
  }
}
EOF
)"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  preview="$(uv run wayfinder-codex --store "$STORE" next --goal-id "$goal_id" --mode=preview)"
  printf '%s' "$preview" | jq -e '.result.recommendation_type == "action"' >/dev/null
  echo "§9.9 wayfinder-codex: preview ok for ${goal_id}"
}

preview_pty() {
  if ! uv run python3 -c "import pexpect" >/dev/null 2>&1; then
    echo "skip §9.8 wayfinder-exec-pty: pexpect not installed"
    return 0
  fi
  local login_script playbook created goal_id dry_run
  login_script="${ROOT}/tests/exec/fixtures/login_prompt.py"
  playbook="${WORKSPACE}/pty_playbook.json"
  jq -n --arg script "$login_script" '{
    rules: [{
      match: {goal_status: "pending", open_recommendation_id: {"$null": true}},
      recommendation: {
        recommendation_type: "action",
        summary: "Login via pty",
        goal_status: "running",
        confidence: 0.9,
        executable: true,
        action: {
          kind: "shell",
          title: "Vendor login",
          shell: {
            argv: ["python3", $script],
            command_for_display: "vendor-cli login",
            cwd: "{workspace_uri}",
            env: {mode: "minimal", set: {}},
            stdin: {mode: "none"},
            pty: true,
            timeout_seconds: 30,
            expected_exit_codes: [0],
            requires_shell: false,
            x_expect_dialogue: [
              {expect: "Username:", send: "svc-deploy"},
              {expect: "Password:", send: "local-pass"},
              {expect: "Session established", then: "eof"}
            ]
          },
          preconditions: [],
          success_criteria: []
        },
        idempotency: {
          level: "strong",
          key: "idem_pty_login",
          scope: "goal",
          safe_to_retry: true,
          safe_to_run_if_already_done: true,
          detects_noop: false,
          dedupe_strategy: "idempotency_key",
          partial_failure_recovery: "retry",
          max_attempts: 1
        },
        risk: {
          level: "low",
          classes: ["execute_local"],
          blast_radius: "workspace",
          requires_approval: false,
          destructive: false,
          network: "not_required",
          secrets: "not_required",
          rollback: {available: false, kind: "unknown", instructions: null}
        }
      }
    }]
  }' >"$playbook"
  created="$(goal_create "Login via pty dialogue." "create_pty_01")"
  goal_id="$(printf '%s' "$created" | jq -r '.result.goal.goal_id')"
  dry_run="$(uv run wayfinder-exec-pty --store "$STORE" --brain-playbook "$playbook" dry-run --goal-id "$goal_id")"
  printf '%s' "$dry_run" | jq -e '.result.stopped_reason == "dry_run"' >/dev/null
  printf '%s' "$dry_run" | jq -e '.result.extensions.pty == true' >/dev/null
  echo "§9.8 wayfinder-exec-pty: dry-run ok for ${goal_id}"
}

preview_make
preview_bt
preview_plan
preview_tw
preview_codex
preview_pty
echo "§9 machines: offline previews complete"
