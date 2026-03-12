# GCP Claude Bridge MCP

A single Cloud Run service that bridges Claude and Google Cloud Platform as an authenticated MCP connector. Version 1.0.0.

## Architecture

```
Container Startup (python -m agent_runner)
├── Load config from /etc/agent-runner/config.yaml or ./agent-config.yaml
├── Merge Firestore config (agents/{name} document)
├── Apply legacy env var mappings (AGENT_NAME, PORT, etc.)
├── Apply env var overrides (AGENT_CONFIG_AGENT__NAME=x)
│
├── Server mode (default):
│   ├── Create AgentRunner (Claude Agent SDK wrapper)
│   ├── Create FastMCP server (run_task, list_peers tools)
│   ├── Create A2A application (Agent Card, task executor)
│   ├── Create OAuth app (if credentials configured)
│   ├── Compose Starlette app (OAuth + A2A + MCP routes)
│   ├── Register in Firestore registry + Pub/Sub
│   └── Start uvicorn
│
├── CLI mode (--task "..."):
│   └── Run single task via AgentRunner, print result, exit
│
└── Worker mode (--worker):
    └── Subscribe to Pub/Sub, process messages as tasks
```

## Project Structure

```
agent-runner/
├── src/agent_runner/
│   ├── __init__.py               # Version: 1.0.0
│   ├── __main__.py               # Entrypoint: python -m agent_runner
│   ├── config.py                 # YAML + Firestore + env overrides + pydantic validation
│   ├── server.py                 # Starlette app composition (FastMCP + A2A + OAuth)
│   ├── agent.py                  # AgentRunner: Claude Agent SDK wrapper
│   ├── hooks/
│   │   ├── registry.py           # Build hooks dict from config
│   │   ├── reflection.py         # Stop hook: persist session learnings to Firestore
│   │   └── audit.py              # PreToolUse hook: log tool invocations as JSON
│   ├── a2a/
│   │   ├── card.py               # Build AgentCard from config
│   │   ├── executor.py           # Bridge A2A tasks to Claude Agent SDK
│   │   ├── client.py             # Call remote agents via A2A protocol + JWT minting
│   │   └── discovery.py          # Firestore registry + Agent Card fetch
│   ├── mcp/
│   │   ├── server.py             # FastMCP v3.1 setup, tool registration
│   │   └── tools.py              # run_task, list_peers tool functions
│   ├── auth/
│   │   ├── oauth.py              # OAuth 2.1 server (RFC 8414, PKCE, stateless JWTs)
│   │   └── middleware.py         # Bearer auth middleware (BearerAuthMiddleware)
│   ├── registry/
│   │   ├── firestore.py          # Agent registry CRUD (database: agents, collection: registry)
│   │   └── pubsub.py             # Capability announcements (topic: agent-capabilities)
│   └── worker/
│       └── pubsub.py             # Pub/Sub background worker mode
│
├── tests/
│   ├── test_config.py            # Config loading, env overrides, Firestore merge
│   ├── test_hooks.py             # Hook registration, audit logging
│   ├── test_a2a.py               # Agent Card building
│   └── test_server.py            # OAuth flow, auth middleware, PKCE
│
├── .github/workflows/
│   ├── ci.yml                    # Lint + test on PR/push to main
│   └── cd.yml                    # Build, push, deploy on main push
│
├── .claude/agents/
│   └── gcloud-operator.md        # Subagent prompt for GCP operations
│
├── pyproject.toml                # Dependencies, ruff config, pytest config
├── Containerfile                 # Multi-stage UBI9-minimal build
├── Dockerfile                    # Symlink → Containerfile
├── Makefile                      # Build, deploy, infra setup targets
├── cloudbuild.yaml               # Cloud Build config
├── agent-config.yaml             # Production config (gcp-claude-bridge)
├── agent-config.example.yaml     # Full config example with documentation
├── .env.example                  # Environment variable template
├── uv.lock                       # Locked dependencies (uv package manager)
├── .gitignore
├── .containerignore
└── .gcloudignore
```

## Development Workflow

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker or Podman (for container builds)
- gcloud CLI (for GCP operations)

### Running Tests

```bash
make test
# or directly:
uv run --with ".[dev]" pytest tests/ -v
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no need for `@pytest.mark.asyncio`).

### Linting

```bash
make lint
# or directly:
uv run --with ".[dev]" ruff check src/ tests/
```

### Local Server

```bash
# In container:
make run-server

# One-shot task:
make run-agent TASK="describe this project"
```

## Code Conventions

- **Python version**: 3.11+ (target in ruff and pyproject.toml)
- **Line length**: 100 characters (ruff)
- **Linter**: ruff (no formatter configured -- only `ruff check`)
- **Type hints**: Use `from __future__ import annotations` for modern union syntax (`str | None`)
- **Config models**: Pydantic v2 `BaseModel` with `Field(default_factory=...)` for mutable defaults
- **Async**: Use `async def` for agent execution and HTTP handlers; `asyncio_mode = "auto"` in tests
- **Error handling**: Firestore/Pub/Sub failures are non-fatal (logged to stderr, never crash the server)
- **Imports**: Standard library → third-party → local; lazy imports for optional GCP clients

## Configuration

### Precedence (lowest to highest)

1. YAML file defaults (`/etc/agent-runner/config.yaml` or `./agent-config.yaml`)
2. Firestore document (`agents/{agent_name}` -- fields: system_prompt, description, model, timeout, max_turns)
3. Legacy env vars (`AGENT_NAME`, `PORT`, `GCP_PROJECT`, `PUBLIC_URL`, `AGENT_TIMEOUT`)
4. `AGENT_CONFIG_*` env var overrides (highest priority)

### Config Overrides

Use `AGENT_CONFIG_<SECTION>__<KEY>=value` (double underscore for nesting):
```
AGENT_CONFIG_AGENT__NAME=my-agent
AGENT_CONFIG_AGENT__MODEL=claude-opus-4-6
AGENT_CONFIG_SERVER__PORT=9090
```

### Legacy Variables (backward compatible)

| Variable | Maps to |
|----------|---------|
| `AGENT_NAME` | `agent.name` |
| `AGENT_TIMEOUT` | `agent.timeout` |
| `GCP_PROJECT` | `gcp.project` |
| `PUBLIC_URL` | `server.public_url` |
| `PORT` | `server.port` |

Legacy vars will NOT override values already set by `AGENT_CONFIG_*` prefixed vars.

### Runtime Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | (required) |
| `GOOGLE_APPLICATION_CREDENTIALS` | SA key file path | (none) |

### OAuth Variables

| Variable | Description |
|----------|-------------|
| `OAUTH_CLIENT_CREDENTIALS` | `client_id:client_secret` |
| `OAUTH_SIGNING_KEY` | RSA private key (PEM) |
| `OAUTH2_AUDIENCE` | JWT audience (defaults to PUBLIC_URL) |
| `OAUTH2_ISSUER` | JWT issuer (defaults to PUBLIC_URL) |
| `OAUTH2_JWKS_URI` | Remote JWKS endpoint (optional) |

If `OAUTH_CLIENT_CREDENTIALS` is unset, authentication is bypassed (local dev mode).

### YAML Config

See `agent-config.example.yaml` for full documentation. Config loads from:
1. `/etc/agent-runner/config.yaml` (container mount)
2. `./agent-config.yaml` (local dev)

### Config Classes (config.py)

| Class | Key Fields |
|-------|------------|
| `AgentConfig` | name, description, model (`claude-sonnet-4-6`), system_prompt, max_turns (50), timeout (600), allowed_tools |
| `McpServerStdioConfig` | type, command, args, env |
| `McpServerUrlConfig` | type, url, headers |
| `SubagentConfig` | type (`local`/`remote`), description, prompt, tools, model, url, discovery |
| `A2AConfig` | enabled (true), skills list |
| `ServerConfig` | host, port (8080), public_url, min_instances |
| `InvocationConfig` | http, streaming, pubsub_enabled, pubsub_subscription |
| `HooksConfig` | reflection (ReflectionHookConfig), audit (AuditHookConfig) |
| `GCPConfig` | project (`claude-connectors`), region (`us-central1`) |

## Dependencies

| Package | Role |
|---------|------|
| `claude-agent-sdk>=0.1.48` | Agent runtime (replaces subprocess claude CLI calls) |
| `fastmcp>=3.1.0` | MCP server (PrefectHQ standalone) |
| `a2a-sdk[http-server]>=0.3.24` | Google A2A protocol |
| `PyJWT[crypto]` | OAuth 2.1 JWT signing/validation |
| `uvicorn` | ASGI server |
| `google-cloud-firestore` | Registry, learnings, config |
| `google-cloud-pubsub` | Capability announcements |
| `httpx` | HTTP client for A2A |
| `pyyaml` | YAML config parsing |
| `pydantic>=2.0` | Config validation |

Dev dependencies: `pytest`, `pytest-asyncio`, `ruff`

## MCP Tools

### `run_task(prompt: str) -> str`
Runs the configured agent via Claude Agent SDK. Timeout controlled by `agent.timeout`.

### `list_peers() -> str`
Queries Firestore `registry` collection for online agents. Filters out self. Returns JSON.

## A2A Protocol

Each agent serves `/.well-known/agent.json` (Agent Card) and accepts A2A tasks.
Remote agents are discovered from the Firestore registry or by direct URL.

### A2A Authentication
All agents share one RSA key pair (from Secret Manager). Agent A mints a JWT with
`iss=aud=B_PUBLIC_URL` signed with the shared key (expires 300s). Agent B validates it.

### API Endpoints

| Route | Auth | Purpose |
|-------|------|---------|
| `/*` | Bearer | FastMCP HTTP streaming (tools) |
| `/.well-known/agent.json` | None | A2A Agent Card |
| `POST /agent/tasks` | Bearer | A2A task submission |
| `GET /.well-known/oauth-authorization-server` | None | OAuth metadata |
| `GET /.well-known/jwks.json` | None | Public JWK |
| `GET /authorize` | None | OAuth authorization code flow (PKCE) |
| `POST /token` | None | OAuth token exchange |
| `GET /.well-known/oauth-protected-resource` | None | Resource info |

Route mounting order: OAuth > A2A > MCP (catch-all mount at `/`).

## Hooks

### Reflection Hook (Stop event)
Two-phase: (1) injects reflection prompt on first Stop, (2) captures reflection text on second Stop and persists to Firestore `session_learnings/{session_id}`.

### Audit Hook (PreToolUse event)
Logs every tool invocation as JSON to stderr: `{event, tool, tool_use_id, timestamp}`. Always passes through (returns None).

## Firestore Schema

**Database**: `agents`

| Collection | Doc ID | Fields |
|------------|--------|--------|
| `registry` | agent_name | name, service_url, capabilities, description, status, last_heartbeat, project |
| `session_learnings` | session_id | session_id, agent_name, timestamp, learnings, duration_seconds, model |
| `task_results` | task_id | task_id, agent_name, prompt, result, timestamp, status |
| `agents` | agent_name | system_prompt, description, model, timeout, max_turns (config overrides) |

## CI/CD

### CI (`.github/workflows/ci.yml`)
- **Trigger**: Push to main, PR to main
- **Concurrency**: Cancels in-progress runs for same PR
- **Steps**: checkout → setup uv + Python 3.11 → `ruff check` → `pytest`

### CD (`.github/workflows/cd.yml`)
- **Trigger**: Push to main only
- **Auth**: Workload Identity Federation (no service account keys)
- **Steps**: checkout → GCP auth → build & push image → deploy `gcp-claude-bridge-mcp` to Cloud Run → set PUBLIC_URL → health check → cleanup old images

## Makefile Targets

### Core
- `build` -- podman/docker build
- `push` -- tag + push to Artifact Registry
- `deploy` -- deploy to Cloud Run (from pre-built image)
- `deploy-source` -- deploy to Cloud Run from source (Cloud Build builds via `cloudbuild.yaml`)
- `test` -- pytest via uv
- `lint` -- ruff check via uv

### Local Development
- `run-server` -- start MCP server in container (port 8080)
- `run-agent TASK="..."` -- one-shot task in container
- `run` -- interactive container shell

### Agent Management
- `register-agent AGENT_FILE=...` -- register agent config from markdown file

### Connection
- `connect` -- register with Claude Code (`claude mcp add-json`)
- `connect-oauth` -- print OAuth credentials + register with Claude Code
- `mcp-json` -- generate `.mcp.json` for project-level auto-connect
- `disconnect` -- remove from Claude Code

### Infrastructure
- `setup-infra` -- idempotent GCP setup (APIs, Artifact Registry, Firestore, Pub/Sub, secrets, IAM)
- `bootstrap` -- full one-command deploy (prereqs → infra → build → push → deploy → configure)
- `bootstrap-source` -- full one-command deploy from source (no local container build)

### Cloud Run Helpers
- `configure-url` -- set PUBLIC_URL from deployed Cloud Run service URL
- `service-url` -- print Cloud Run service URL

### Credentials
- `show-credentials` -- display MCP URL, OAuth client ID/secret
- `rotate-oauth` -- regenerate OAuth credentials + signing key

### Makefile Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROJECT` | `claude-connectors` | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `REPO` | `agent-runner` | Artifact Registry repo |
| `IMAGE` | `gcp-claude-bridge-mcp` | Container image name |
| `SERVICE` | `$AGENT_ID` or `$IMAGE` | Cloud Run service name |
| `SA_NAME` | `claude-connector` | Service account name |
| `AGENT_ID` | (empty) | Agent name override |
| `CONTAINER_RUNTIME` | auto-detect docker/podman | Container build tool |

## Containerfile

Multi-stage build on UBI9-minimal:
1. `base` -- system packages (which, tar, gzip, xz, findutils, git)
2. `install-node` -- Node.js 22.14.0 LTS (required by Claude Agent SDK)
3. `install-gcloud` -- Google Cloud SDK (pruned)
4. `install-python` -- uv + Python 3.11
5. `install-app` -- pip install from pyproject.toml, copy config + agent prompts

Entrypoint: `python3.11 -m agent_runner`
Runs as UID 1001:1001 (non-root).

## Key Lessons

- `ubi-minimal` ships `curl-minimal`; do NOT add `curl` (package conflict)
- gcloud SDK: use direct tarball, not the installer script
- Set `CLOUDSDK_PYTHON=/usr/local/bin/python3.11` for gcloud compatibility
- `--concurrency=10` on Cloud Run (claude subprocess is the bottleneck)
- `--set-env-vars` replaces ALL env vars; use `--update-env-vars` for partial updates
- JWKS deadlock: use local public key directly (not HTTP fetch) at concurrency=1
- Firestore/Pub/Sub failures are non-fatal (server starts, logs warnings)
- A2A self-delegation: rejected to prevent deadlock
- OAuth auth codes are stateless JWTs (survives Cloud Run scale-to-zero)
- PublicURLMiddleware auto-derives public_url from Host header on first request
- Container runtime auto-detects docker then falls back to podman
