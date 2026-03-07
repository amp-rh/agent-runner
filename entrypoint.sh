#!/bin/bash
set -e

CLAUDE_CONFIG_SOURCE="${CLAUDE_CONFIG:-/run/config/claude.json}"
if [ -f "$CLAUDE_CONFIG_SOURCE" ]; then
    mkdir -p "$HOME/.claude"
    cp "$CLAUDE_CONFIG_SOURCE" "$HOME/.claude.json"
fi

if [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    gcloud auth activate-service-account --key-file="$GOOGLE_APPLICATION_CREDENTIALS" 2>/dev/null || true
fi

if [ -n "$AGENT_ID" ]; then
    AGENT_NAME=$(python3.11 /usr/local/lib/mcp-server/agent_loader.py)
    export AGENT_NAME
fi
AGENT_NAME="${AGENT_NAME:-gcloud-operator}"
export AGENT_NAME

if [ "${MODE}" = "server" ]; then
    python3.11 /usr/local/lib/mcp-server/agent_registry.py &
    exec python3.11 /usr/local/lib/mcp-server/server.py
elif [ $# -gt 0 ]; then
    exec claude --print --dangerously-skip-permissions --agent "$AGENT_NAME" "$*"
else
    exec claude
fi
