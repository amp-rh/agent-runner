# Agent Runner v2

A reusable container image for deploying Claude agents as MCP servers on Cloud Run.

## Architecture

```
Container Startup (python -m agent_runner)
├── Load config from /etc/agent-runner/config.yaml or ./agent-config.yaml
├── Apply env var overrides (AGENT_CONFIG_AGENT__NAME=x)
├── Apply legacy env vars (AGENT_NAME, PORT, etc.)
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
src/agent_runner/
├── __init__.py
├── __main__.py           # Entrypoint: python -m agent_runner
├── config.py             # YAML + env overrides + pydantic validation
├── server.py             # Starlette app composition (FastMCP + A2A + OAuth)
├── agent.py              # ClaudeSDKClient wrapper
├── hooks/
│   ├── reflection.py     # Stop hook: persist session learnings to Firestore
│   ├── audit.py          # PreToolUse hook: log tool invocations
│   └── registry.py       # Build hooks dict from config
├── a2a/
│   ├── card.py           # Build AgentCard from config
│   ├── executor.py       # Bridge A2A tasks to Claude Agent SDK
│   ├── client.py         # Call remote agents via A2A protocol
│   └── discovery.py      # Firestore registry + Agent Card fetch
├── mcp/
│   ├── server.py         # FastMCP v3.1 setup, tool registration
│   └── tools.py          # run_task, list_peers tool functions
├── auth/
│   ├── oauth.py          # OAuth 2.1 server (RFC 8414, PKCE, stateless JWTs)
│   └── middleware.py     # Bearer auth middleware
├── registry/
│   ├── firestore.py      # Agent registry CRUD
│   └── pubsub.py         # Capability announcements
└── worker/
    └── pubsub.py         # Pub/Sub background worker mode
```

## Dependencies

| Package | Role |
|---------|------|
| `claude-agent-sdk>=0.1.48` | Agent runtime (replaces subprocess claude CLI calls) |
| `fastmcp>=3.1.0` | MCP server (PrefectHQ standalone) |
| `a2a-sdk[http-server]>=0.3.24` | Google A2A protocol |
| `PyJWT[crypto]` | OAuth 2.1 JWT signing/validation |
| `uvicorn` | ASGI server |
| `google-cloud-firestore` | Registry, learnings |
| `google-cloud-pubsub` | Capability announcements |
| `httpx` | HTTP client for A2A |
| `pyyaml` | YAML config parsing |
| `pydantic>=2.0` | Config validation |

## Environment Variables

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

## YAML Config

See `agent-config.example.yaml` for full documentation. Config loads from:
1. `/etc/agent-runner/config.yaml` (container mount)
2. `./agent-config.yaml` (local dev)

Then env var overrides are applied.

## MCP Tools

### `run_task(prompt: str) -> str`
Runs the configured agent via Claude Agent SDK. Timeout controlled by `agent.timeout`.

### `list_peers() -> str`
Queries Firestore `registry` collection for online agents. Filters out self.

## A2A Protocol

Each agent serves `/.well-known/agent.json` (Agent Card) and accepts A2A tasks.
Remote agents are discovered from the Firestore registry or by direct URL.

### A2A Authentication
All agents share one RSA key pair (from Secret Manager). Agent A mints a JWT with
`iss=aud=B_PUBLIC_URL` signed with the shared key. Agent B validates it.

## Hooks

### Reflection Hook (Stop event)
Asks the agent to reflect on the session, then persists learnings to Firestore
at `session_learnings/{session_id}`.

### Audit Hook (PreToolUse event)
Logs every tool invocation as JSON to stderr.

## Firestore Schema

**Database**: `agents`

**Collection `registry`**: Live agent instances
- name, service_url, capabilities, description, status, last_heartbeat, project

**Collection `session_learnings`**: Session reflections
- session_id, agent_name, timestamp, learnings, duration_seconds, model

## Makefile Targets

### Core
- `build` -- podman build
- `push` -- tag + push to Artifact Registry
- `deploy` -- deploy to Cloud Run (from pre-built image)
- `deploy-source` -- deploy to Cloud Run from source (Cloud Build builds via `cloudbuild.yaml`)
- `test` -- pytest
- `lint` -- ruff check

### Local Development
- `run-server` -- start MCP server in container
- `run-agent TASK="..."` -- one-shot task in container

### Connection
- `connect` -- register with Claude Code (`claude mcp add-json`)
- `connect-oauth` -- print OAuth credentials + register with Claude Code
- `mcp-json` -- generate `.mcp.json` for project-level auto-connect
- `disconnect` -- remove from Claude Code

### Infrastructure
- `setup-infra` -- idempotent GCP setup (enables Cloud Build, grants SA permissions)
- `bootstrap` -- full one-command deploy (build + push + deploy from image)
- `bootstrap-source` -- full one-command deploy from source (no local build required)

### Credentials
- `show-credentials` -- display OAuth client ID/secret
- `rotate-oauth` -- regenerate OAuth credentials

## Containerfile

Multi-stage build on UBI9-minimal:
1. `base` -- system packages (which, tar, gzip, findutils)
2. `install-node` -- Node.js 22 LTS (required by Claude Agent SDK)
3. `install-gcloud` -- Google Cloud SDK (pruned)
4. `install-python` -- uv + Python 3.11
5. `install-app` -- pip install from pyproject.toml

Entrypoint: `python3.11 -m agent_runner`

## Key Lessons

- `ubi-minimal` ships `curl-minimal`; do NOT add `curl` (package conflict)
- gcloud SDK: use direct tarball, not the installer script
- Set `CLOUDSDK_PYTHON=/usr/local/bin/python3.11` for gcloud compatibility
- `--concurrency=10` on Cloud Run (claude subprocess is the bottleneck)
- `--set-env-vars` replaces ALL env vars; use `--update-env-vars` for partial updates
- JWKS deadlock: use local public key directly (not HTTP fetch) at concurrency=1
- Firestore/Pub/Sub failures are non-fatal (server starts, logs warnings)
- A2A self-delegation: rejected to prevent deadlock
