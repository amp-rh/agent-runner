# Agent Runner

Deploy Claude agents as MCP servers on Google Cloud Run with A2A communication, session reflection hooks, and auto-registration with Claude Code.

## Quick Start

### Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- `podman` for container builds
- `python3` and `openssl`
- `ANTHROPIC_API_KEY` env var set

### One-Command Deploy

```bash
make bootstrap
```

This sets up GCP infrastructure, builds the container, deploys to Cloud Run, and prints your MCP server URL and OAuth credentials.

### Connect to Claude Code

```bash
make connect          # Register with Claude Code CLI
make connect-oauth    # Print OAuth credentials for Claude.ai web
make mcp-json         # Generate .mcp.json for project-level auto-connect
```

## Architecture

Each deployed agent is simultaneously:
- An **MCP server** (callers invoke tools via FastMCP Streamable HTTP)
- An **MCP client** (consumes external MCP servers defined in YAML config)
- An **A2A server** (serves Agent Card, accepts A2A tasks)
- An **A2A client** (discovers and calls remote agents)

### Stack

| Component | Package |
|-----------|---------|
| Agent runtime | `claude-agent-sdk` |
| MCP server | `fastmcp` (PrefectHQ) |
| A2A protocol | `a2a-sdk` (Google) |
| Auth | `PyJWT` (OAuth 2.1, stateless JWTs) |
| Config | `pydantic` + `pyyaml` |

## Configuration

Edit `agent-config.yaml` or use env vars:

```yaml
agent:
  name: "my-agent"
  model: "claude-sonnet-4-6"
  max_turns: 50
  timeout: 300
```

See `agent-config.example.yaml` for full documentation.

## Development

```bash
make build                          # Build container image
make run-server                     # Start MCP server locally
make run-agent TASK="list buckets"  # One-shot CLI task
make test                           # Run tests
make lint                           # Run linter
```

## Deploy a Custom Agent

```bash
# Edit agent-config.yaml with your agent settings
make build
make push
make deploy
make configure-url    # First deploy only
make connect          # Register with Claude Code
```

## Credential Management

```bash
make show-credentials   # Display OAuth client ID/secret
make rotate-oauth       # Regenerate credentials (requires re-deploy)
```
