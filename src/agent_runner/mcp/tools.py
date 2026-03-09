"""MCP tool functions exposed to external callers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runner.agent import AgentRunner
    from agent_runner.config import AppConfig

# Module-level references injected by mcp/server.py
_agent_runner: AgentRunner | None = None
_config: AppConfig | None = None


def configure(agent_runner: AgentRunner, config: AppConfig) -> None:
    """Inject dependencies for tool functions."""
    global _agent_runner, _config
    _agent_runner = agent_runner
    _config = config


async def run_task(prompt: str) -> str:
    """Run a task using the configured Claude agent."""
    if _agent_runner is None:
        raise RuntimeError("Agent runner not initialized")
    return await _agent_runner.run(prompt)


def list_peers() -> str:
    """List available peer agents from the Firestore registry."""
    try:
        from agent_runner.registry.firestore import discover

        config = _config
        if config is None:
            return json.dumps({"error": "config not loaded"})

        peers = discover(project=config.gcp.project)
        # Filter out self
        agent_name = config.agent.name
        peers = [p for p in peers if p.get("name") != agent_name]
        return json.dumps(peers, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
