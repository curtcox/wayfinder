#!/usr/bin/env bash
# Shared helpers for examples/*/run.sh scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
FIXTURES="${ROOT}/examples/fixtures"

require_scripted() {
  for arg in "$@"; do
    if [[ "$arg" == "--scripted" ]]; then
      return 0
    fi
  done
  echo "This example requires --scripted for CI determinism (or configure a live LLM)." >&2
  exit 1
}

require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for this example (install via brew install jq or apt install jq)." >&2
    exit 1
  fi
}

wf() {
  uv run wayfinder "$@"
}

wfe() {
  uv run wayfinder-exec "$@"
}

new_workspace() {
  WORKSPACE="$(mktemp -d)"
  export WORKSPACE
  STORE="${WORKSPACE}/store"
  export STORE
  cleanup() {
    rm -rf "$WORKSPACE"
  }
  trap cleanup EXIT
}

goal_create() {
  local description="$1"
  local create_id="$2"
  wf --store "$STORE" --brain-playbook "$PLAYBOOK" goal create <<EOF
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "${create_id}",
  "created_at": "2026-07-05T12:00:00Z",
  "actor": {"type": "human", "id": "example", "authority": "owner", "authenticated": true},
  "description": "${description}",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "low"}
}
EOF
}
