"""Claude Agent SDK wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


class AgentRunner:
    """Wraps ClaudeSDKClient with config-driven options."""

    def __init__(self, config: AppConfig, hooks: dict | None = None):
        self._config = config
        self._hooks = hooks

    def _build_options(self) -> ClaudeAgentOptions:
        cfg = self._config.agent
        allowed_tools = list(cfg.allowed_tools)

        # Enable Agent tool if subagents are defined
        if self._config.subagents and "Agent" not in allowed_tools:
            allowed_tools.append("Agent")

        opts = ClaudeAgentOptions(
            model=cfg.model,
            system_prompt=cfg.system_prompt or None,
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",
            max_turns=cfg.max_turns,
            mcp_servers=_build_mcp_servers(self._config),
            agents=_build_subagents(self._config),
        )

        if self._hooks:
            opts.hooks = self._hooks

        return opts

    async def run(self, prompt: str) -> str:
        """Run a task and return the text result."""
        import asyncio

        options = self._build_options()
        client = ClaudeSDKClient(options=options)
        timeout_seconds = self._config.agent.timeout

        texts: list[str] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                await client.connect(prompt)
                async for message in client.receive_messages():
                    if isinstance(message, ResultMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                texts.append(block.text)
        except TimeoutError:
            return f"Task timed out after {timeout_seconds}s. Consider increasing agent.timeout."
        except Exception as exc:
            return f"Task failed: {type(exc).__name__}: {exc}"
        finally:
            await client.disconnect()

        return "\n".join(texts)


def _build_mcp_servers(config: AppConfig) -> dict:
    """Build mcp_servers dict for ClaudeAgentOptions from config."""
    servers = {}
    for name, srv_config in config.mcp_servers.items():
        srv_type = srv_config.get("type", "stdio")
        if srv_type == "url" or srv_type == "http":
            servers[name] = {
                "type": "http",
                "url": srv_config["url"],
            }
            if "headers" in srv_config:
                servers[name]["headers"] = srv_config["headers"]
        elif srv_type == "stdio":
            servers[name] = {
                "command": srv_config["command"],
                "args": srv_config.get("args", []),
            }
            if "env" in srv_config:
                servers[name]["env"] = srv_config["env"]
    return servers


def _build_subagents(config: AppConfig) -> dict[str, AgentDefinition] | None:
    """Build subagent definitions from config (local type only)."""
    agents = {}
    for name, sub_config in config.subagents.items():
        if sub_config.type != "local":
            continue
        model = None
        if sub_config.model:
            # Map full model IDs to short names
            model_map = {
                "claude-opus-4-6": "opus",
                "claude-sonnet-4-6": "sonnet",
                "claude-haiku-4-5": "haiku",
            }
            model = model_map.get(sub_config.model, sub_config.model)
        agents[name] = AgentDefinition(
            description=sub_config.description,
            prompt=sub_config.prompt,
            tools=sub_config.tools or None,
            model=model,
        )
    return agents or None
