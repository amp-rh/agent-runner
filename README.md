# Agent Runner

**Deploy any Claude agent as a production service in one command.** Agent Runner is a single container image that exposes Claude agents via both MCP (Model Context Protocol) and A2A (Agent-to-Agent) protocols — so agents can be called by humans, other agents, or any MCP-compatible client. Built for the [agent-runner platform](https://github.com/amp-rh/agent-runner) on Google Cloud Run.

## Prerequisites

- [Podman](https://podman.io/) or Docker
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Start

```sh
# Build the image
make build

# Run with default agent
make run ANTHROPIC_API_KEY=sk-ant-...

# Run with a custom agent prompt
make run AGENT_PROMPT_FILE=my-agent.md
```

The server starts on port 8080 with two protocol endpoints:

| Protocol | Endpoint | Description |
|----------|----------|-------------|
| MCP | `http://localhost:8080/mcp` | Streamable HTTP MCP server with `run_task` tool |
| A2A | `http://localhost:8080/a2a` | JSON-RPC task execution |
| A2A Card | `http://localhost:8080/.well-known/agent.json` | Agent capability discovery |

## Configuration

Agents are configured via environment variables and/or a YAML config file.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_NAME` | `agent` | Agent name |
| `AGENT_DESCRIPTION` | `Claude agent` | Human-readable description |
| `AGENT_TIMEOUT` | `300` | Subprocess timeout (seconds) |
| `PORT` | `8080` | Server listen port |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required) |

### YAML Config

Mount a YAML file at `/run/agent/config.yaml` or set `AGENT_CONFIG_FILE`:

```yaml
name: my-assistant
system_prompt: |
    You are a helpful coding assistant.
mcp_servers:
    filesystem:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
```

### System Prompt File

Mount a markdown file and set `AGENT_PROMPT_FILE`:

```sh
podman run --rm -p 8080:8080 \
  -e ANTHROPIC_API_KEY \
  -e AGENT_NAME=my-agent \
  -v ./my-agent.md:/run/agent/system_prompt.md:ro \
  agent-runner:latest
```

## Extending

Build a derived image to bake in an agent definition:

```dockerfile
FROM agent-runner:latest
COPY my-agent.md /home/user/.claude/agents/my-agent.md
ENV AGENT_NAME=my-agent
```

## Development

```sh
# Run tests
make test

# Build and run locally
make build && make run
```
