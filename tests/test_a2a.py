"""Tests for A2A Agent Card, executor, and client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent_runner.a2a.card import build_agent_card
from agent_runner.config import AppConfig, load_config


def _make_config(**overrides) -> AppConfig:
    data = {"agent": {"name": "test-agent"}}
    data.update(overrides)
    return AppConfig(**data)


# ---- Agent Card tests ----


def test_build_agent_card_defaults():
    """Agent card builds from default config."""
    config = load_config(path="/nonexistent.yaml")
    card = build_agent_card(config)

    assert card.name == "gcp-claude-bridge"
    assert card.version == "1.0.0"
    assert card.url == "http://localhost:8080"
    assert len(card.skills) == 1
    assert card.skills[0].id == "run_task"


def test_build_agent_card_custom(tmp_path):
    """Agent card reflects custom config."""
    import yaml

    cfg = {
        "agent": {"name": "custom-agent", "description": "My custom agent"},
        "server": {"public_url": "https://example.com"},
        "a2a": {
            "skills": [
                {
                    "id": "analyze",
                    "name": "Analyze data",
                    "description": "Run data analysis",
                    "tags": ["data", "analysis"],
                }
            ]
        },
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    config = load_config(path=cfg_file)
    card = build_agent_card(config)

    assert card.name == "custom-agent"
    assert card.url == "https://example.com"
    assert card.skills[0].id == "analyze"
    assert "data" in card.skills[0].tags


# ---- Executor tests ----


class TestClaudeAgentExecutor:
    async def test_execute_extracts_prompt_and_runs(self):
        from a2a.types import TextPart

        from agent_runner.a2a.executor import ClaudeAgentExecutor

        runner = AsyncMock()
        runner.run = AsyncMock(return_value="Agent output")
        executor = ClaudeAgentExecutor(runner)

        context = MagicMock()
        context.task_id = "task-1"
        context.context_id = "ctx-1"
        context.message = MagicMock()
        context.message.parts = [TextPart(text="Hello agent")]

        event_queue = AsyncMock()

        await executor.execute(context, event_queue)

        runner.run.assert_awaited_once_with("Hello agent")
        event_queue.enqueue_event.assert_awaited_once()
        event = event_queue.enqueue_event.call_args[0][0]
        assert event.artifact.parts[0].root.text == "Agent output"

    async def test_execute_no_prompt(self):
        from agent_runner.a2a.executor import ClaudeAgentExecutor

        runner = AsyncMock()
        runner.run = AsyncMock(return_value="fallback")
        executor = ClaudeAgentExecutor(runner)

        context = MagicMock()
        context.task_id = "task-1"
        context.context_id = "ctx-1"
        context.message = None

        event_queue = AsyncMock()

        await executor.execute(context, event_queue)
        runner.run.assert_awaited_once_with("No prompt provided.")

    async def test_execute_handles_error(self):
        from a2a.types import TextPart

        from agent_runner.a2a.executor import ClaudeAgentExecutor

        runner = AsyncMock()
        runner.run = AsyncMock(side_effect=ValueError("boom"))
        executor = ClaudeAgentExecutor(runner)

        context = MagicMock()
        context.task_id = "task-1"
        context.context_id = "ctx-1"
        context.message = MagicMock()
        context.message.parts = [TextPart(text="test")]
        event_queue = AsyncMock()

        await executor.execute(context, event_queue)

        event = event_queue.enqueue_event.call_args[0][0]
        assert "Error: boom" in event.artifact.parts[0].root.text

    async def test_cancel_sends_canceled_status(self):
        from agent_runner.a2a.executor import ClaudeAgentExecutor

        runner = AsyncMock()
        executor = ClaudeAgentExecutor(runner)

        context = MagicMock()
        context.task_id = "task-1"
        context.context_id = "ctx-1"
        event_queue = AsyncMock()

        await executor.cancel(context, event_queue)

        event_queue.enqueue_event.assert_awaited_once()
        event = event_queue.enqueue_event.call_args[0][0]
        assert event.final is True


# ---- Client tests ----


class TestMintPeerToken:
    def test_mint_peer_token_creates_valid_jwt(self):
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        from agent_runner.a2a.client import mint_peer_token
        from agent_runner.auth.oauth import OAuthConfig

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        oauth_config = OAuthConfig(
            client_id="test-client",
            client_secret="test-secret",
            signing_key=key,
            public_url="http://localhost:8080",
        )

        token = mint_peer_token(oauth_config, "https://peer.example.com")
        assert token is not None

        claims = pyjwt.decode(
            token,
            key.public_key(),
            algorithms=["RS256"],
            audience="https://peer.example.com",
        )
        assert claims["iss"] == "https://peer.example.com"
        assert claims["aud"] == "https://peer.example.com"
        assert claims["sub"] == "test-client"
        assert "jti" in claims

    def test_mint_peer_token_returns_none_without_config(self):
        from agent_runner.a2a.client import mint_peer_token

        result = mint_peer_token(None, "https://peer.example.com")
        assert result is None


class TestCallRemoteAgent:
    async def test_call_remote_agent_sends_task(self):
        from agent_runner.a2a.client import call_remote_agent

        mock_a2a_client = AsyncMock()
        mock_response = MagicMock()
        mock_artifact = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "remote result"
        mock_artifact.parts = [mock_part]
        mock_response.result = MagicMock()
        mock_response.result.artifacts = [mock_artifact]

        mock_a2a_client.send_task = AsyncMock(return_value=mock_response)

        with patch("a2a.client.A2AClient") as MockA2A:
            MockA2A.get_client_from_agent_card_url = AsyncMock(
                return_value=mock_a2a_client
            )

            result = await call_remote_agent(
                "https://peer.example.com",
                "do something",
                token="test-token",
            )

        assert result == "remote result"
