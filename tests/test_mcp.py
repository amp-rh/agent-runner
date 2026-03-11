"""Tests for MCP tool functions."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent_runner.config import AppConfig
from agent_runner.mcp.tools import configure, list_peers, run_task


def _make_config(**overrides) -> AppConfig:
    data = {"agent": {"name": "test-agent"}}
    data.update(overrides)
    return AppConfig(**data)


class TestRunTask:
    async def test_run_task_delegates_to_runner(self):
        runner = AsyncMock()
        runner.run = AsyncMock(return_value="result text")
        config = _make_config()

        configure(runner, config)
        result = await run_task("do something")

        assert result == "result text"
        runner.run.assert_awaited_once_with("do something")

    async def test_run_task_not_initialized(self):
        # Reset module state
        import agent_runner.mcp.tools as tools_mod
        tools_mod._agent_runner = None

        with pytest.raises(RuntimeError, match="not initialized"):
            await run_task("test")


class TestListPeers:
    def test_list_peers_returns_json(self):
        config = _make_config()
        configure(AsyncMock(), config)

        peers = [
            {"name": "peer-1", "service_url": "https://peer1.example.com"},
            {"name": "test-agent", "service_url": "https://self.example.com"},
        ]

        with patch("agent_runner.registry.firestore.discover", return_value=peers):
            result = list_peers()

        data = json.loads(result)
        # Should filter out self
        assert len(data) == 1
        assert data[0]["name"] == "peer-1"

    def test_list_peers_config_not_loaded(self):
        import agent_runner.mcp.tools as tools_mod
        tools_mod._config = None
        tools_mod._agent_runner = AsyncMock()

        result = list_peers()
        data = json.loads(result)
        assert "error" in data

    def test_list_peers_firestore_error(self):
        config = _make_config()
        configure(AsyncMock(), config)

        with patch(
            "agent_runner.registry.firestore.discover",
            side_effect=Exception("Firestore down"),
        ):
            result = list_peers()

        data = json.loads(result)
        assert "error" in data
        assert "Firestore down" in data["error"]
