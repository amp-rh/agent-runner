"""Tests for AgentRunner and helper functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent_runner.config import AppConfig, SubagentConfig


def _make_config(**overrides) -> AppConfig:
    """Build an AppConfig with optional overrides."""
    data = {"agent": {"name": "test-agent", "timeout": 10}}
    data.update(overrides)
    return AppConfig(**data)


class TestBuildMcpServers:
    """Tests for _build_mcp_servers()."""

    def test_empty_config(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config()
        assert _build_mcp_servers(config) == {}

    def test_stdio_server(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config(mcp_servers={
            "my-server": {"type": "stdio", "command": "node", "args": ["server.js"]},
        })
        servers = _build_mcp_servers(config)
        assert "my-server" in servers
        assert servers["my-server"]["command"] == "node"
        assert servers["my-server"]["args"] == ["server.js"]

    def test_stdio_server_with_env(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config(mcp_servers={
            "s": {"type": "stdio", "command": "cmd", "env": {"FOO": "bar"}},
        })
        servers = _build_mcp_servers(config)
        assert servers["s"]["env"] == {"FOO": "bar"}

    def test_url_server(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config(mcp_servers={
            "remote": {"type": "url", "url": "https://example.com/mcp"},
        })
        servers = _build_mcp_servers(config)
        assert servers["remote"]["type"] == "http"
        assert servers["remote"]["url"] == "https://example.com/mcp"

    def test_http_server(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config(mcp_servers={
            "remote": {"type": "http", "url": "https://x.com", "headers": {"Auth": "tok"}},
        })
        servers = _build_mcp_servers(config)
        assert servers["remote"]["headers"] == {"Auth": "tok"}

    def test_default_type_is_stdio(self):
        from agent_runner.agent import _build_mcp_servers

        config = _make_config(mcp_servers={
            "s": {"command": "cmd"},
        })
        servers = _build_mcp_servers(config)
        assert "command" in servers["s"]


class TestBuildSubagents:
    """Tests for _build_subagents()."""

    def test_empty_config(self):
        from agent_runner.agent import _build_subagents

        config = _make_config()
        assert _build_subagents(config) is None

    def test_local_subagent(self):
        from agent_runner.agent import _build_subagents

        config = _make_config(subagents={
            "helper": SubagentConfig(
                type="local",
                description="A helper",
                prompt="You are a helper.",
                tools=["Read"],
            ),
        })
        agents = _build_subagents(config)
        assert agents is not None
        assert "helper" in agents
        assert agents["helper"].description == "A helper"
        assert agents["helper"].prompt == "You are a helper."
        assert agents["helper"].tools == ["Read"]

    def test_remote_subagent_skipped(self):
        from agent_runner.agent import _build_subagents

        config = _make_config(subagents={
            "remote": SubagentConfig(type="remote", url="https://example.com"),
        })
        assert _build_subagents(config) is None

    def test_model_mapping(self):
        from agent_runner.agent import _build_subagents

        config = _make_config(subagents={
            "a": SubagentConfig(type="local", description="d", model="claude-opus-4-6"),
        })
        agents = _build_subagents(config)
        assert agents["a"].model == "opus"

    def test_unknown_model_passthrough(self):
        from agent_runner.agent import _build_subagents

        config = _make_config(subagents={
            "a": SubagentConfig(type="local", description="d", model="custom-model"),
        })
        agents = _build_subagents(config)
        assert agents["a"].model == "custom-model"


class TestAgentRunnerBuildOptions:
    """Tests for AgentRunner._build_options()."""

    def test_basic_options(self):
        from agent_runner.agent import AgentRunner

        config = _make_config()
        runner = AgentRunner(config)
        opts = runner._build_options()
        assert opts.model == "claude-sonnet-4-6"
        assert opts.max_turns == 50
        assert opts.permission_mode == "bypassPermissions"

    def test_agent_tool_added_when_subagents(self):
        from agent_runner.agent import AgentRunner

        config = _make_config(subagents={
            "sub": SubagentConfig(type="local", description="d"),
        })
        runner = AgentRunner(config)
        opts = runner._build_options()
        assert "Agent" in opts.allowed_tools

    def test_agent_tool_not_duplicated(self):
        from agent_runner.agent import AgentRunner

        config = _make_config(
            agent={"name": "t", "timeout": 10, "allowed_tools": ["Read", "Agent"]},
            subagents={"sub": SubagentConfig(type="local", description="d")},
        )
        runner = AgentRunner(config)
        opts = runner._build_options()
        assert opts.allowed_tools.count("Agent") == 1

    def test_hooks_attached(self):
        from agent_runner.agent import AgentRunner

        config = _make_config()
        hooks = {"Stop": []}
        runner = AgentRunner(config, hooks=hooks)
        opts = runner._build_options()
        assert opts.hooks == hooks

    def test_system_prompt_none_when_empty(self):
        from agent_runner.agent import AgentRunner

        config = _make_config(agent={"name": "t", "timeout": 10, "system_prompt": ""})
        runner = AgentRunner(config)
        opts = runner._build_options()
        assert opts.system_prompt is None


class TestAgentRunnerRun:
    """Tests for AgentRunner.run()."""

    async def test_run_returns_text(self):
        from claude_agent_sdk import ResultMessage, TextBlock

        from agent_runner.agent import AgentRunner

        config = _make_config()
        runner = AgentRunner(config)

        text_block = TextBlock(text="Hello world")
        mock_result = MagicMock(spec=ResultMessage)
        mock_result.content = [text_block]
        # Make isinstance(message, ResultMessage) work
        mock_result.__class__ = ResultMessage

        with patch("agent_runner.agent.ClaudeSDKClient") as MockSDK:
            mock_client = MagicMock()
            MockSDK.return_value = mock_client
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()

            async def fake_messages():
                yield mock_result

            mock_client.receive_messages.return_value = fake_messages()

            result = await runner.run("test prompt")
            assert result == "Hello world"
            mock_client.connect.assert_awaited_once_with("test prompt")
            mock_client.disconnect.assert_awaited_once()

    async def test_run_timeout(self):
        from agent_runner.agent import AgentRunner

        config = _make_config(agent={"name": "t", "timeout": 0})
        runner = AgentRunner(config)

        with patch("agent_runner.agent.ClaudeSDKClient") as MockSDK:
            mock_client = AsyncMock()
            MockSDK.return_value = mock_client
            mock_client.connect = AsyncMock(side_effect=TimeoutError)
            mock_client.disconnect = AsyncMock()

            # Use timeout=0 which should trigger quickly
            result = await runner.run("test")
            assert "timed out" in result or "Task failed" in result

    async def test_run_exception(self):
        from agent_runner.agent import AgentRunner

        config = _make_config()
        runner = AgentRunner(config)

        with patch("agent_runner.agent.ClaudeSDKClient") as MockSDK:
            mock_client = AsyncMock()
            MockSDK.return_value = mock_client
            mock_client.connect = AsyncMock(side_effect=ValueError("SDK error"))
            mock_client.disconnect = AsyncMock()

            result = await runner.run("test")
            assert "Task failed" in result
            assert "ValueError" in result
            assert "SDK error" in result

    async def test_run_empty_response(self):
        from agent_runner.agent import AgentRunner

        config = _make_config()
        runner = AgentRunner(config)

        with patch("agent_runner.agent.ClaudeSDKClient") as MockSDK:
            mock_client = MagicMock()
            MockSDK.return_value = mock_client
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()

            async def empty_messages():
                return
                yield  # noqa: F841

            mock_client.receive_messages.return_value = empty_messages()

            result = await runner.run("test")
            assert result == ""
