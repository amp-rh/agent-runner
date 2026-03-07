# Agent Runner

A reusable container image for deploying Claude agents as MCP servers on Cloud Run.

## Project Purpose

Build a slim UBI9-based container image pre-loaded with:
- Google Cloud SDK (`gcloud`, `gsutil`, `bq`)
- Python 3.11 + `uv` for Python tooling
- Claude CLI for AI-assisted workflows
- MCP server with OAuth 2.1 for remote agent access
- Firestore-based agent configuration and peer discovery

## Architecture

```
entrypoint.sh
  -> agent_loader.py    # Fetches agent config from Firestore
  -> agent_registry.py  # Advertises capabilities via Pub/Sub + Firestore
  -> server.py          # MCP server (Streamable HTTP + OAuth 2.1)
     -> claude CLI      # Runs the configured agent
```

## File Structure

```
Containerfile          # Multi-stage container build (UBI9-minimal)
server.py              # MCP server with OAuth 2.1 auth
oauth.py               # OAuth 2.1 authorization server
agent_loader.py        # Firestore agent config loader + register CLI
agent_registry.py      # Pub/Sub + Firestore agent discovery
entrypoint.sh          # Container entrypoint
Makefile               # Build, deploy, and management targets
.claude/agents/        # Baked-in fallback agent definitions
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT` | Target GCP project | `claude-connectors` |
| `AGENT_ID` | Agent to load from Firestore on startup | (none, uses fallback) |
| `AGENT_NAME` | Agent name (set automatically by loader) | `gcloud-operator` |
| `AGENT_TIMEOUT` | Subprocess timeout in seconds | `300` |
| `AGENT_CAPABILITIES` | Comma-separated capability tags | (empty) |
| `AGENT_DESCRIPTION` | Human-readable agent description | (auto) |
| `MODE` | `server` for MCP server, omit for CLI | (empty) |
| `PUBLIC_URL` | External URL for OAuth metadata | `http://localhost:8080` |
| `PORT` | Server listen port | `8080` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to SA key file | (none) |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | (none) |

## Bootstrap (Full Idempotent Deploy)

Deploy everything with a single command. Safe to run repeatedly — skips resources that already exist and preserves existing OAuth credentials.

```bash
make bootstrap
```

This runs: `_check-prereqs` -> `setup-infra` -> `build` -> `push` -> `_register-orchestrator` -> `deploy` -> `configure-url` -> report credentials.

On completion, outputs the orchestrator's MCP server URL, OAuth client ID, and client secret for connecting to Claude Web.

### Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- `podman` for container builds
- `python3` and `openssl`
- `ANTHROPIC_API_KEY` env var set (first run only)

### Deploy a Different Agent

```bash
make register-agent AGENT_FILE=.claude/agents/my-agent.md
make push
make deploy AGENT_ID=my-agent
make configure-url  # first deploy only
```

### Credential Management

```bash
make show-credentials          # display current OAuth credentials
make rotate-oauth              # generate new credentials (requires re-deploy)
```

## Agent Discovery

Agents advertise capabilities to a shared Pub/Sub topic (`agent-capabilities`) on startup and register in the Firestore `registry` collection. Other agents can discover peers via the `list_peers` MCP tool.

## Firestore Schema

### `agents` database -> `agents` collection

Each document represents an agent configuration:
- `name`, `description`, `model`, `system_prompt`, `enabled`, `timeout`

### `agents` database -> `registry` collection

Each document represents a live agent instance:
- `name`, `service_url`, `capabilities`, `description`, `status`, `last_heartbeat`

## Makefile Variables

All configurable via environment:
- `PROJECT` (default: `claude-connectors`)
- `REGION` (default: `us-central1`)
- `IMAGE` (default: `agent-runner`)
- `SERVICE` (default: `agent-runner-mcp`)
- `SA_NAME` (default: `claude-connector`)
- `SA_EMAIL` (default: `$SA_NAME@$PROJECT.iam.gserviceaccount.com`)
- `SA_KEY_FILE` (default: `sa-key.json`)
- `FIRESTORE_LOCATION` (default: `nam5`)

## Containerfile Conventions

- Multi-stage builds with named stages (`install-<tool>`)
- Each stage builds on the previous, adding one tool
- Clean caches and remove `__pycache__` after each install
- Tools installed to standard paths; PATH updated via ENV
- `USER 1001:1001` set only in the final stage

## Secrets (Secret Manager)

| Secret | Description | Created by |
|--------|-------------|------------|
| `gcloud-sa-key` | GCP service account key (mounted as file) | `setup-infra` |
| `ANTHROPIC_API_KEY` | Anthropic API key (env var) | `setup-infra` |
| `oauth-client-credentials` | `client_id:client_secret` for OAuth (env var) | `setup-infra` (auto-generated) |
| `oauth-signing-key` | RSA private key for JWT signing (env var) | `setup-infra` (auto-generated) |

OAuth secrets are only generated on first run. Use `make rotate-oauth` to regenerate.

## Key Lessons

- `ubi-minimal` ships `curl-minimal`; do NOT add `curl` (conflicts)
- gcloud SDK: use direct tarball, not the installer script
- Set `CLOUDSDK_PYTHON=/usr/local/bin/python3.11` (gcloud needs 3.10+)
- Claude CLI installs to `~/.local/bin`; copy to `/usr/local/bin` in same RUN
- `UV_INSTALL_DIR=/usr/local/bin` for world-accessible uv
- `--concurrency=10` on Cloud Run (Claude subprocess is the bottleneck)
- `setup-infra` is idempotent: uses describe-then-create for all resources, only adds secret versions when none exist
- `--set-env-vars` replaces ALL env vars; use `--update-env-vars` for updates (see `configure-url`)
