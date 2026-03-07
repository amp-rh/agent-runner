# Agent Runner

A reusable container image for deploying [Claude](https://claude.ai) agents as MCP servers on Google Cloud Run.

## What It Does

Agent Runner packages Claude CLI, Google Cloud SDK, and an MCP server into a single container image. You define agents as markdown files, register them in Firestore, and deploy each as its own Cloud Run service. Agents can discover and (eventually) delegate tasks to each other via Pub/Sub and a shared Firestore registry.

## Prerequisites

- A GCP project with billing enabled
- [Podman](https://podman.io/) or Docker
- `gcloud` CLI authenticated locally
- A GCP service account with appropriate permissions
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Start

### 1. One-time infrastructure setup

```bash
make setup-infra
```

This enables required GCP APIs, creates an Artifact Registry repo, sets up Secret Manager secrets, and creates the Pub/Sub topic for agent discovery.

### 2. Build the image

```bash
make build
```

### 3. Register an agent

Agent configurations are markdown files with YAML frontmatter:

```markdown
---
name: my-agent
description: "Does useful things"
model: opus
---

You are a helpful agent that...
```

Push it to Firestore:

```bash
make register-agent AGENT_FILE=.claude/agents/my-agent.md
```

### 4. Deploy to Cloud Run

```bash
make push
make deploy AGENT_ID=my-agent
make configure-url  # first deploy only
```

### 5. Connect via MCP

The deployed service exposes a Streamable HTTP MCP endpoint with OAuth 2.1 auth at the Cloud Run service URL.

## Local Development

Run the MCP server locally:

```bash
make run-server AGENT_ID=my-agent
```

Run the agent interactively:

```bash
make run
```

Run a one-shot task:

```bash
make run-agent TASK="list all Cloud Run services" AGENT_ID=gcloud-operator
```

## Configuration

All settings are configurable via environment variables or Makefile overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT` | `claude-connectors` | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `IMAGE` | `agent-runner` | Container image name |
| `SERVICE` | `agent-runner-mcp` | Cloud Run service name |
| `AGENT_ID` | (none) | Agent to load from Firestore |

Example with overrides:

```bash
PROJECT=my-project REGION=europe-west1 make deploy AGENT_ID=my-agent
```

## Agent Discovery

When running in server mode, each agent:

1. Announces its capabilities to a shared Pub/Sub topic (`agent-capabilities`)
2. Registers itself in the Firestore `registry` collection with its URL and capabilities
3. Can query the registry to discover other agents via the `list_peers` MCP tool

Set capabilities via the `AGENT_CAPABILITIES` environment variable (comma-separated tags).

## Architecture

```
Container (UBI9-minimal)
+-- Google Cloud SDK (gcloud, gsutil, bq)
+-- Python 3.11 (via uv)
+-- Claude CLI
+-- MCP Server (Starlette + uvicorn)
    +-- OAuth 2.1 (RFC 8414, PKCE, stateless JWTs)
    +-- run_task tool -> claude --agent <name>
    +-- list_peers tool -> Firestore registry query
```

**Firestore databases:**
- `agents` database / `agents` collection: agent configurations (name, model, system prompt)
- `agents` database / `registry` collection: live agent endpoints and capabilities

## Included Agents

### gcloud-operator

A GCP operations specialist with full gcloud, gsutil, and bq access. Enforces free-tier limits and security best practices. Included as a baked-in fallback.

## License

MIT
