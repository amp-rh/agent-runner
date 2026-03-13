"""FastMCP v3.1 server setup and tool registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from agent_runner.agent import AgentRunner
    from agent_runner.config import AppConfig


def create_mcp_server(config: AppConfig, agent_runner: AgentRunner) -> FastMCP:
    """Create and configure the FastMCP server with tools."""
    from agent_runner.mcp import tools

    tools.configure(agent_runner, config)

    mcp = FastMCP(
        f"{config.agent.name}-mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def run_task(prompt: str) -> str:
        """Run a task using the configured Claude agent."""
        return await tools.run_task(prompt)

    @mcp.tool()
    def list_peers() -> str:
        """List available peer agents from the registry."""
        return tools.list_peers()

    return mcp
