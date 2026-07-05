#!/usr/bin/env bash
# Shared helpers for examples/*/run.sh scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
FIXTURES="${ROOT}/examples/fixtures"
EXAMPLE_USER="$(id -un 2>/dev/null || echo example)"
export EXAMPLE_USER

is_scripted() {
  for arg in "$@"; do
    if [[ "$arg" == "--scripted" ]]; then
      return 0
    fi
  done
  return 1
}

require_scripted() {
  if is_scripted "$@"; then
    return 0
  fi
  echo "This example requires --scripted for CI determinism (or configure a live LLM)." >&2
  exit 1
}

skip_if_scripted() {
  local reason="$1"
  shift
  if is_scripted "$@"; then
    echo "skip ${reason}"
    exit 0
  fi
}

require_live_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing ${name}; export it or configure credentials for this live-only example." >&2
    exit 1
  fi
}

tcp_reachable() {
  local host="$1"
  local port="$2"
  uv run python3 -c "
import socket
s = socket.socket()
s.settimeout(1.0)
try:
    s.connect(('${host}', ${port}))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
" 2>/dev/null
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
  "actor": {"type": "human", "id": "${EXAMPLE_USER}", "authority": "owner", "authenticated": true},
  "description": "${description}",
  "workspace_uri": "file:${WORKSPACE}/project",
  "policy": {"max_auto_risk_level": "low"}
}
EOF
}

last_issued_event() {
  local goal_id="$1"
  wf --store "$STORE" history --goal-id "$goal_id" --since-seq 0 \
    | jq -c 'select(.type == "recommendation.issued")' | tail -1
}

wayfinder_with_playbook_cmd() {
  local playbook="$1"
  uv run python3 -c "import shlex, sys; print(shlex.join([sys.executable, '-m', 'wayfinder.cli', '--brain-playbook', sys.argv[1]]))" "$playbook"
}
