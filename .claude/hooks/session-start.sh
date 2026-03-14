#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python dependencies using uv
if command -v uv &>/dev/null; then
  echo "Installing Python dependencies..."
  uv pip install --system -e ".[dev]" --quiet
else
  echo "uv not found, skipping Python dependency install"
fi

# Populate GH_TOKEN from GCP Secret Manager if not already set
if [ -z "${GH_TOKEN:-}" ]; then
  if command -v gcloud &>/dev/null; then
    GCP_PROJECT="${GCP_PROJECT:-claude-connectors}"
    echo "Fetching GitHub token from Secret Manager (project: ${GCP_PROJECT})..."
    if GH_TOKEN=$(gcloud secrets versions access latest --secret=github-token --project="${GCP_PROJECT}" 2>/dev/null); then
      export GH_TOKEN
      echo "GH_TOKEN populated from Secret Manager"
    else
      echo "WARNING: Could not fetch github-token from Secret Manager (project: ${GCP_PROJECT})" >&2
    fi
  else
    echo "WARNING: gcloud not found; cannot fetch GH_TOKEN from Secret Manager" >&2
  fi
fi

# Authenticate gh CLI using GH_TOKEN if available and not already logged in
if [ -n "${GH_TOKEN:-}" ]; then
  if ! gh auth status &>/dev/null; then
    echo "Authenticating gh CLI..."
    echo "$GH_TOKEN" | gh auth login --with-token
    echo "gh CLI authenticated as: $(gh api user --jq '.login')"
  else
    echo "gh CLI already authenticated"
  fi
else
  echo "WARNING: GH_TOKEN is not set. gh CLI will not be authenticated." >&2
  echo "Set GH_TOKEN in your Claude Code web environment settings to enable gh CLI login." >&2
fi
