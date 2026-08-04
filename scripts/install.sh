#!/usr/bin/env bash
# local-vibes self-installer — safe for an LLM (or you) to run.
#
#   scripts/install.sh                         # generic default backend (Ollama)
#   LOCAL_VIBES_BASE_URL=http://127.0.0.1:8080/v1 \
#   LOCAL_VIBES_MODEL=my-model scripts/install.sh   # point at any OpenAI-compatible server
#
# Installs three removable things (all undone by scripts/uninstall.sh):
#   1. the local-vibes MCP server  (via `claude mcp add`, user scope)
#   2. the local-vibes plugin      (skill + targeted hook — NOT added to any CLAUDE.md)
#   3. its marketplace registration
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BASE_URL="${LOCAL_VIBES_BASE_URL:-http://localhost:11434/v1}"
MODEL="${LOCAL_VIBES_MODEL:-qwen3-coder:30b}"
CONC="${LOCAL_VIBES_MAX_CONCURRENCY:-8}"
TEMP="${LOCAL_VIBES_TEMPERATURE:-0}"

echo "==> building the MCP server"
cd "$HERE"
npm install --silent
npm run build

echo "==> registering the plugin (skill + hook)"
claude plugin marketplace add "$HERE" 2>&1 | tail -1 || true
claude plugin install local-vibes@local-vibes 2>&1 | tail -2 || true

echo "==> registering the MCP server  (backend: $MODEL @ $BASE_URL)"
claude mcp remove local-vibes -s user >/dev/null 2>&1 || true
claude mcp add local-vibes -s user \
  -e "LOCAL_VIBES_BASE_URL=$BASE_URL" \
  -e "LOCAL_VIBES_MODEL=$MODEL" \
  -e "LOCAL_VIBES_MAX_CONCURRENCY=$CONC" \
  -e "LOCAL_VIBES_TEMPERATURE=$TEMP" \
  -- node "$HERE/dist/index.js"

echo
echo "local-vibes installed  (backend: $MODEL @ $BASE_URL)"
echo "  • MCP tool 'delegate' + the local-vibes skill load in your NEXT claude session."
echo "  • Point at a different backend: re-run with LOCAL_VIBES_BASE_URL / LOCAL_VIBES_MODEL set."
echo "  • Remove everything: scripts/uninstall.sh"
