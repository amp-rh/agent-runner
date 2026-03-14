#!/bin/bash
set -euo pipefail

# SessionStart hook for agent-runner
# Sets up GitHub CLI authentication and installs dev dependencies

# Install Python dev dependencies using uv
# This ensures pytest and ruff are available for testing and linting
uv sync --all-extras --dev 2>&1

# Export GitHub token to session environment
# The token should be set in Claude Code web environment variables
if [ -n "${GITHUB_TOKEN:-}" ]; then
    echo "export GITHUB_TOKEN='${GITHUB_TOKEN}'" >> "$CLAUDE_ENV_FILE"
fi

if [ -n "${GH_TOKEN:-}" ]; then
    echo "export GH_TOKEN='${GH_TOKEN}'" >> "$CLAUDE_ENV_FILE"
fi

echo "✓ Session setup complete: dependencies installed and GitHub CLI configured"
