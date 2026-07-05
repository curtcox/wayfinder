#!/usr/bin/env bash
# Wayfinder setup script (Phase 9).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="full"
for arg in "$@"; do
  case "$arg" in
    --minimal) MODE="minimal" ;;
    --full) MODE="full" ;;
    -h|--help)
      cat <<'EOF'
Usage: ./setup.sh [--minimal|--full]

  --minimal  Install core CLI tools only.
  --full     Install core tools plus §9 machine extras (default).
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [[ "$MODE" == "full" ]]; then
  uv sync --extra dev --extra docs --extra machines
else
  uv sync --extra dev --extra docs
fi

CONFIG_DIR="${HOME}/.config/wayfinder"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "${CONFIG_DIR}/config.toml" ]]; then
  cat >"${CONFIG_DIR}/config.toml" <<'EOF'
# Wayfinder local configuration.
# LLM settings are optional for scripted-brain workflows.

# [llm]
# base_url = "https://openrouter.ai/api/v1"
# api_key = "..."
# model = "openai/gpt-4.1-mini"
EOF
  chmod 600 "${CONFIG_DIR}/config.toml"
fi

echo
echo "Running wayfinder doctor..."
uv run wayfinder doctor

echo
echo "Setup complete. Try: uv run wayfinder capabilities"
