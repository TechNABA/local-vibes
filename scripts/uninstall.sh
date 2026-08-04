#!/usr/bin/env bash
# Remove everything install.sh added. Leaves the repo itself intact.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"

echo "==> removing the MCP server"
claude mcp remove local-vibes -s user 2>&1 | tail -1 || true

echo "==> uninstalling the plugin (skill + hook)"
claude plugin uninstall local-vibes 2>&1 | tail -1 || true

echo "==> removing the marketplace registration"
claude plugin marketplace remove local-vibes 2>&1 | tail -1 || true

echo
echo "local-vibes removed (MCP server + plugin + marketplace). No CLAUDE.md was ever modified."
echo "The next claude session will start without it."
