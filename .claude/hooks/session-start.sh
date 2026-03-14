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

# Install gcloud CLI if not present
if ! command -v gcloud &>/dev/null; then
  GCLOUD_DIR="${HOME}/google-cloud-sdk"
  if [ ! -f "${GCLOUD_DIR}/bin/gcloud" ]; then
    echo "Installing gcloud CLI..."
    curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/google-cloud-sdk.tar.gz \
      | tar -xz -C "${HOME}"
  fi
  export PATH="${GCLOUD_DIR}/bin:${PATH}"
  # Use python3.11 if available (matches container setup)
  if command -v python3.11 &>/dev/null; then
    export CLOUDSDK_PYTHON=python3.11
  fi
fi

# Authenticate gcloud using service account credentials
if ! gcloud auth print-access-token &>/dev/null; then
  if [ -n "${GOOGLE_SERVICE_ACCOUNT_KEY:-}" ]; then
    # JSON key content provided as env var — write to a temp file and activate
    SA_KEY_FILE=$(mktemp /tmp/sa-key-XXXXXX.json)
    echo "${GOOGLE_SERVICE_ACCOUNT_KEY}" > "${SA_KEY_FILE}"
    echo "Activating gcloud service account from GOOGLE_SERVICE_ACCOUNT_KEY..."
    gcloud auth activate-service-account --key-file="${SA_KEY_FILE}" --quiet
    export GOOGLE_APPLICATION_CREDENTIALS="${SA_KEY_FILE}"
  elif [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
    echo "Activating gcloud service account from GOOGLE_APPLICATION_CREDENTIALS..."
    gcloud auth activate-service-account --key-file="${GOOGLE_APPLICATION_CREDENTIALS}" --quiet
  else
    echo "WARNING: No GCP credentials found (set GOOGLE_SERVICE_ACCOUNT_KEY or GOOGLE_APPLICATION_CREDENTIALS)" >&2
  fi
fi

# Set default project
GCP_PROJECT="${GCP_PROJECT:-claude-connectors}"
gcloud config set project "${GCP_PROJECT}" --quiet 2>/dev/null || true

# Populate GH_TOKEN from GCP Secret Manager if not already set
if [ -z "${GH_TOKEN:-}" ]; then
  echo "Fetching GitHub token from Secret Manager (project: ${GCP_PROJECT})..."
  if GH_TOKEN=$(gcloud secrets versions access latest --secret=github-token --project="${GCP_PROJECT}" 2>/dev/null); then
    export GH_TOKEN
    echo "GH_TOKEN populated from Secret Manager"
  else
    echo "WARNING: Could not fetch github-token from Secret Manager (project: ${GCP_PROJECT})" >&2
  fi
fi

# Install gh CLI if not present
if ! command -v gh &>/dev/null; then
  GH_DIR="${HOME}/.local/gh"
  if [ ! -f "${GH_DIR}/bin/gh" ]; then
    echo "Installing gh CLI..."
    GH_VERSION=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest \
      | grep '"tag_name"' | sed 's/.*"v\([^"]*\)".*/\1/')
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
      | tar -xz -C /tmp
    mkdir -p "${GH_DIR}/bin"
    mv "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" "${GH_DIR}/bin/gh"
    rm -rf "/tmp/gh_${GH_VERSION}_linux_amd64"
    echo "gh CLI ${GH_VERSION} installed"
  fi
  export PATH="${GH_DIR}/bin:${PATH}"
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
