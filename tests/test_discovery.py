"""Tests for agent discovery via Firestore registry and Agent Cards."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent_runner.a2a.discovery import discover_peers, fetch_agent_card, resolve_remote_subagent
from agent_runner.config import AppConfig, SubagentConfig


def _make_config(**overrides) -> AppConfig:
    data = {"agent": {"name": "test-agent"}, "gcp": {"project": "test-project"}}
    data.update(overrides)
    return AppConfig(**data)


class TestFetchAgentCard:
    async def test_fetch_agent_card_success(self):
        card_data = {"name": "remote-agent", "version": "1.0"}
        mock_resp = MagicMock()
        mock_resp.json.return_value = card_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("agent_runner.a2a.discovery.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_agent_card("https://example.com")

        assert result == card_data
        mock_client.get.assert_awaited_once_with(
            "https://example.com/.well-known/agent.json"
        )

    async def test_fetch_agent_card_strips_trailing_slash(self):
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("agent_runner.a2a.discovery.httpx.AsyncClient", return_value=mock_client):
            await fetch_agent_card("https://example.com/")

        mock_client.get.assert_awaited_once_with(
            "https://example.com/.well-known/agent.json"
        )

    async def test_fetch_agent_card_failure(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("agent_runner.a2a.discovery.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_agent_card("https://bad.example.com")

        assert result is None


class TestDiscoverPeers:
    def test_discover_peers_excludes_self(self):
        peers = [
            {"name": "test-agent", "service_url": "https://self.example.com"},
            {"name": "other-agent", "service_url": "https://other.example.com"},
        ]
        config = _make_config()

        with patch("agent_runner.registry.firestore.discover", return_value=peers):
            result = discover_peers(config, exclude_self=True)

        assert len(result) == 1
        assert result[0]["name"] == "other-agent"

    def test_discover_peers_includes_self(self):
        peers = [
            {"name": "test-agent", "service_url": "https://self.example.com"},
            {"name": "other-agent", "service_url": "https://other.example.com"},
        ]
        config = _make_config()

        with patch("agent_runner.registry.firestore.discover", return_value=peers):
            result = discover_peers(config, exclude_self=False)

        assert len(result) == 2


class TestResolveRemoteSubagent:
    def test_resolve_by_url(self):
        config = _make_config(subagents={
            "remote-agent": SubagentConfig(
                type="remote",
                url="https://remote.example.com",
            ),
        })
        result = resolve_remote_subagent(config, "remote-agent")
        assert result == "https://remote.example.com"

    def test_resolve_unknown_agent(self):
        config = _make_config()
        result = resolve_remote_subagent(config, "nonexistent")
        assert result is None

    def test_resolve_by_registry(self):
        config = _make_config(subagents={
            "remote-agent": SubagentConfig(
                type="remote",
                discovery="registry",
            ),
        })
        peers = [
            {"name": "remote-agent", "service_url": "https://discovered.example.com"},
        ]

        with patch("agent_runner.registry.firestore.discover", return_value=peers):
            result = resolve_remote_subagent(config, "remote-agent")

        assert result == "https://discovered.example.com"

    def test_resolve_registry_not_found(self):
        config = _make_config(subagents={
            "remote-agent": SubagentConfig(type="remote", discovery="registry"),
        })

        with patch("agent_runner.registry.firestore.discover", return_value=[]):
            result = resolve_remote_subagent(config, "remote-agent")

        assert result is None
