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
CONFIG_FILE="${CONFIG_DIR}/config.toml"
SECRETS_FILE="${CONFIG_DIR}/secrets.toml"
mkdir -p "$CONFIG_DIR"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  cat >"${CONFIG_FILE}" <<'EOF'
# Wayfinder local configuration.
# LLM settings are optional for scripted-brain workflows.

# [llm]
# base_url = "https://openrouter.ai/api/v1"
# api_key = "..."
# model = "openai/gpt-4.1-mini"
EOF
  chmod 600 "${CONFIG_FILE}"
fi

if [[ ! -f "${SECRETS_FILE}" ]]; then
  cat >"${SECRETS_FILE}" <<'EOF'
# Local secret values for secret_ref resolution (mode 0600).
# Example:
# github_token = "ghp_..."
EOF
  chmod 600 "${SECRETS_FILE}"
fi

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local answer=""
  if [[ ! -t 0 ]]; then
    return 1
  fi
  if [[ "$default" == "y" ]]; then
    read -r -p "${prompt} [Y/n] " answer
    answer="${answer:-y}"
  else
    read -r -p "${prompt} [y/N] " answer
    answer="${answer:-n}"
  fi
  case "$answer" in
    y|Y|yes|Yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

append_config_value() {
  local key="$1"
  local value="$2"
  if grep -q "^${key} = " "${CONFIG_FILE}" 2>/dev/null; then
    return 0
  fi
  if ! grep -q '^\[llm\]' "${CONFIG_FILE}" 2>/dev/null; then
    printf '\n[llm]\n' >>"${CONFIG_FILE}"
  fi
  printf '%s = "%s"\n' "$key" "$value" >>"${CONFIG_FILE}"
}

append_secret_value() {
  local key="$1"
  local value="$2"
  if grep -q "^${key} = " "${SECRETS_FILE}" 2>/dev/null; then
    return 0
  fi
  printf '%s = "%s"\n' "$key" "$value" >>"${SECRETS_FILE}"
}

configure_credentials() {
  if ! prompt_yes_no "Configure an LLM endpoint for live brains?"; then
    return 0
  fi

  echo "Presets: 1) OpenRouter  2) OpenAI  3) Ollama (localhost)  4) custom"
  read -r -p "Choose preset [1-4]: " preset
  case "${preset:-4}" in
    1)
      base_url="https://openrouter.ai/api/v1"
      model="openai/gpt-4.1-mini"
      ;;
    2)
      base_url="https://api.openai.com/v1"
      model="gpt-4.1-mini"
      ;;
    3)
      base_url="http://localhost:11434/v1"
      model="llama3.2"
      ;;
    *)
      read -r -p "LLM base URL: " base_url
      read -r -p "LLM model name: " model
      ;;
  esac

  read -r -s -p "LLM API key (leave blank for local servers): " api_key
  echo
  append_config_value "base_url" "${base_url}"
  append_config_value "model" "${model}"
  if [[ -n "${api_key}" ]]; then
    append_config_value "api_key" "${api_key}"
  fi

  if prompt_yes_no "Store GITHUB_TOKEN for wayfinder-bridge gh?"; then
    read -r -s -p "GITHUB_TOKEN: " github_token
    echo
    append_secret_value "github_token" "${github_token}"
    echo "Export before using the bridge: export GITHUB_TOKEN=\"\${your-token}\""
  fi

  if prompt_yes_no "Store BROWSERBASE_API_KEY for wayfinder-web?"; then
    read -r -s -p "BROWSERBASE_API_KEY: " browserbase_key
    echo
    append_secret_value "browserbase_api_key" "${browserbase_key}"
    echo "Export before using wayfinder-web: export BROWSERBASE_API_KEY=\"\${your-key}\""
  fi
}

configure_credentials

if [[ "$MODE" == "full" ]]; then
  echo "Optional system tools (install manually if doctor reports them missing):"
  echo "  jq make task ansible-playbook gh temporal ffmpeg curl"
fi

echo
echo "Running wayfinder doctor..."
uv run wayfinder doctor

echo
echo "Setup complete. Try: uv run wayfinder capabilities"
echo "Scripted guide examples: make examples-scripted"
