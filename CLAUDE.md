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
Container Startup (entrypoint.sh)
├─ Load claude.json config (if provided at /run/config/claude.json)
├─ Activate gcloud service account auth
├─ Load AGENT_ID from Firestore via agent_loader.py
│  └─ Falls back to gcloud-operator if Firestore unavailable
│
├─ MODE=server:
│  ├─ agent_registry.py (background thread)
│  │  ├─ Publish capabilities to Pub/Sub (agent-capabilities topic)
│  │  └─ Upsert agent entry in Firestore registry collection
│  └─ server.py (foreground, uvicorn on 0.0.0.0:PORT)
│     ├─ BearerAuthMiddleware validates JWT on every MCP request
│     ├─ OAuth endpoints: /authorize, /token, /well-known/*
│     └─ MCP tools: run_task, list_peers
│
└─ Default (CLI mode):
   └─ Execute: claude --print --agent $AGENT_NAME "$@"
```

## File Structure

```
Containerfile          # Multi-stage container build (UBI9-minimal)
server.py              # MCP server with OAuth 2.1 auth
oauth.py               # OAuth 2.1 authorization server (RFC 8414)
agent_loader.py        # Firestore agent config loader + register CLI
agent_registry.py      # Pub/Sub + Firestore agent discovery
entrypoint.sh          # Container entrypoint
Makefile               # Build, deploy, and management targets
.claude/agents/        # Baked-in fallback agent definitions
  gcloud-operator.md   # GCP operations agent (default fallback)
  orchestrator.md      # Agent lifecycle orchestrator
```

## Environment Variables

### Runtime (container)

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT` | Target GCP project | `claude-connectors` |
| `AGENT_ID` | Agent to load from Firestore on startup | (none, uses fallback) |
| `AGENT_NAME` | Agent name passed to claude CLI | `gcloud-operator` |
| `AGENT_TIMEOUT` | Subprocess timeout in seconds | `300` |
| `AGENT_CAPABILITIES` | Comma-separated capability tags | (empty) |
| `AGENT_DESCRIPTION` | Human-readable agent description | (auto from Firestore) |
| `MODE` | `server` for MCP server, omit for CLI | (empty) |
| `PUBLIC_URL` | External URL for OAuth metadata (issuer + audience) | `http://localhost:8080` |
| `PORT` | Server listen port | `8080` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to SA key file | (none) |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | (none) |

### OAuth (injected from Secret Manager)

| Variable | Description |
|----------|-------------|
| `OAUTH_CLIENT_CREDENTIALS` | `client_id:client_secret` (colon-separated) |
| `OAUTH_SIGNING_KEY` | RSA private key (PEM) for JWT signing |
| `OAUTH2_AUDIENCE` | JWT audience override (defaults to `PUBLIC_URL`) |
| `OAUTH2_ISSUER` | JWT issuer override (defaults to `PUBLIC_URL`) |
| `OAUTH2_JWKS_URI` | Remote JWKS endpoint for validation (optional) |

**Note**: If `OAUTH_CLIENT_CREDENTIALS` is unset, authentication is bypassed (local dev mode).

### Makefile Variables (all overridable via environment)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT` | `claude-connectors` | GCP project for all operations |
| `REGION` | `us-central1` | GCP region for Artifact Registry + Cloud Run |
| `REPO` | `agent-runner` | Artifact Registry repository name |
| `IMAGE` | `agent-runner` | Container image name |
| `SERVICE` | `$(IMAGE)-mcp` | Cloud Run service name |
| `SA_NAME` | `claude-connector` | Service account name |
| `SA_EMAIL` | `$(SA_NAME)@$(PROJECT).iam.gserviceaccount.com` | Full SA email |
| `SA_KEY_FILE` | `sa-key.json` | Local SA key file path |
| `FIRESTORE_LOCATION` | `nam5` | Firestore multi-region location (North America) |

## Bootstrap (Full Idempotent Deploy)

Deploy everything with a single command. Safe to run repeatedly — skips resources that already exist and preserves existing OAuth credentials.

```bash
make bootstrap
```

This runs in sequence:
1. `_check-prereqs` — Validates gcloud, podman, python3, openssl, authentication
2. `setup-infra` — Idempotent GCP setup (APIs, Artifact Registry, Firestore, Pub/Sub, secrets, SA, IAM)
3. `build` — Builds container image locally with podman
4. `push` — Tags and pushes to Artifact Registry
5. `_register-orchestrator` — Registers orchestrator agent config in Firestore
6. `_deploy-orchestrator` — Deploys to Cloud Run
7. `_configure-and-report` — Sets `PUBLIC_URL`, prints credentials

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

### Common Development Commands

```bash
make build                                     # Build image locally
make secret                                    # Create podman secret from sa-key.json
make run                                       # Interactive container shell
make run-agent TASK="list my GCS buckets"      # One-shot task, CLI mode
make run-server AGENT_ID=gcloud-operator       # Start MCP server on localhost:8080
make show-credentials                          # Display current OAuth credentials
make rotate-oauth                              # Regenerate OAuth credentials (requires re-deploy)
```

### Credential Management

```bash
make show-credentials          # display current OAuth credentials
make rotate-oauth              # generate new credentials (requires re-deploy)
```

## Agent Definition Format

Agent definitions are markdown files with YAML frontmatter. They live in `.claude/agents/` and are registered to Firestore via `make register-agent`.

```markdown
---
name: my-agent
description: "Human-readable description for peer discovery"
model: claude-opus-4-6        # or claude-sonnet-4-6, etc.
color: purple                 # optional, for UI display
memory: project               # optional: "project" for persistent memory, omit for none
timeout: 600                  # optional, overrides AGENT_TIMEOUT
---

Agent system prompt goes here. Full markdown body.
Supports multi-line instructions.
```

**Parsed fields**: `name`, `description`, `model`, `color`, `memory`, `timeout`
**System prompt**: Everything after the closing `---` frontmatter fence

`agent_loader.py` parses these files (line-by-line YAML, no external YAML parser needed) and stores them in Firestore. On container startup, `AGENT_ID` is used to fetch the config from Firestore and write it back to a `.md` file for the claude CLI.

## MCP Tools

### `run_task(prompt: str) -> str`

Runs the configured agent against a prompt:
```
claude --print --dangerously-skip-permissions --agent $AGENT_NAME <prompt>
```
- Timeout controlled by `AGENT_TIMEOUT` (default 300s)
- Output is the agent's full text response
- Subprocess errors surface as MCP tool errors

### `list_peers() -> str`

Queries the Firestore `registry` collection for all agents with `status="online"`, returns JSON array:
```json
[{"name": "...", "service_url": "...", "capabilities": [...], "description": "..."}]
```

## OAuth 2.1 Implementation

`oauth.py` implements a complete single-tenant OAuth 2.1 server (RFC 8414) for protecting the MCP endpoint:

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /.well-known/oauth-authorization-server` | OAuth metadata (RFC 8414) |
| `GET /.well-known/oauth-protected-resource` | Protected resource metadata |
| `GET /.well-known/jwks.json` | Public key for JWT validation |
| `GET /authorize` | Authorization endpoint (auto-approves, issues auth code) |
| `POST /token` | Token endpoint (exchanges auth code for access token) |

### Flow

1. Client fetches OAuth metadata to discover endpoints
2. Client redirects to `/authorize` with `code_challenge` (PKCE, S256)
3. Server auto-approves (single-tenant) and redirects with signed JWT auth code (120s TTL)
4. Client POSTs to `/token` with `code_verifier`; server validates PKCE and issues access token JWT (3600s TTL)
5. Client sends `Authorization: Bearer <token>` on MCP requests
6. `BearerAuthMiddleware` validates JWT (RS256, checks iss/aud/exp) on every request

### Statelessness (Critical for Scale-to-Zero)

Auth codes and access tokens are **signed JWTs**, not session state. This means:
- No database needed for OAuth state
- Cloud Run instances can scale to zero between requests
- JWT payload carries all validation data (redirect_uri, code_challenge, expiry, jti)

## Agent Discovery

Agents advertise capabilities to a shared Pub/Sub topic (`agent-capabilities`) on startup and register in the Firestore `registry` collection. Other agents can discover peers via the `list_peers` MCP tool.

## Firestore Schema

### Database: `agents`

**Collection: `agents`** — Agent configurations

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Agent identifier (document ID) |
| `description` | string | Human-readable description |
| `model` | string | Claude model ID |
| `system_prompt` | string | Full system prompt |
| `enabled` | bool | Whether agent is active |
| `timeout` | int | Subprocess timeout override |
| `created_at` | timestamp | Creation time |
| `updated_at` | timestamp | Last update time |

**Collection: `registry`** — Live agent instances

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Agent name |
| `service_url` | string | Cloud Run service URL |
| `capabilities` | string[] | Capability tags |
| `description` | string | Human-readable description |
| `status` | string | `"online"` or `"offline"` |
| `last_heartbeat` | timestamp | Last registration time |
| `project` | string | GCP project ID |

## Containerfile Conventions

- Multi-stage builds with named stages (`install-<tool>`)
- Each stage builds on the previous, adding one tool
- Aggressive cleanup: remove docs, caches, `__pycache__` after each install
- Tools installed to standard paths; PATH updated via `ENV`
- `USER 1001:1001` set **only** in the final stage
- Python 3.11 via `uv`; `CLOUDSDK_PYTHON` set for gcloud compatibility

### Build Stages

| Stage | Content |
|-------|---------|
| `base` | UBI9-minimal with system packages (which, tar, gzip) |
| `install-gcloud` | Google Cloud SDK via tarball, pruned for size |
| `install-python` | uv + Python 3.11 |
| `install-claude` | Claude CLI (copied to /usr/local/bin) |
| `final` | Python packages + application code, nonroot user |

## Secrets (Secret Manager)

| Secret | Description | Created by |
|--------|-------------|------------|
| `gcloud-sa-key` | GCP service account key (mounted as file) | `setup-infra` |
| `ANTHROPIC_API_KEY` | Anthropic API key (env var) | `setup-infra` |
| `oauth-client-credentials` | `client_id:client_secret` for OAuth (env var) | `setup-infra` (auto-generated) |
| `oauth-signing-key` | RSA private key for JWT signing (env var) | `setup-infra` (auto-generated) |

OAuth secrets are only generated on first run. Use `make rotate-oauth` to regenerate.

## Cloud Run Configuration

When deployed via `make deploy`:
- **Memory**: 512Mi
- **CPU**: 1 vCPU
- **Concurrency**: 10 (claude subprocess is the bottleneck)
- **Min instances**: 0 (scale to zero)
- **Max instances**: 1
- **Timeout**: 300s
- **Auth**: `--allow-unauthenticated` (OAuth middleware guards the MCP tools)

## Key Lessons & Gotchas

- `ubi-minimal` ships `curl-minimal`; do **NOT** add `curl` (package conflict)
- gcloud SDK: use direct tarball, **not** the installer script (more reliable and deterministic)
- Set `CLOUDSDK_PYTHON=/usr/local/bin/python3.11` — gcloud requires Python 3.10+
- Claude CLI installs to `~/.local/bin`; copy to `/usr/local/bin` in **same** `RUN` layer
- `UV_INSTALL_DIR=/usr/local/bin` for world-accessible `uv` binary
- `--concurrency=10` on Cloud Run — the claude subprocess (not HTTP) is the bottleneck
- `setup-infra` is idempotent: uses describe-then-create for all resources; only adds secret versions when none exist
- `--set-env-vars` **replaces ALL** env vars; use `--update-env-vars` for partial updates (see `configure-url` target)
- **JWKS deadlock**: With `concurrency=1`, a self-referential JWKS HTTP fetch deadlocks; solved by using the local public key directly in `oauth.py` instead of fetching from self
- Firestore and Pub/Sub failures in `agent_registry.py` are non-fatal — the server starts and logs warnings
- `agent_loader.py` uses simple line-by-line YAML parsing (no `pyyaml` dependency needed in the container)
