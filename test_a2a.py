"""Integration test for agent-to-agent communication via delegate_task.

Starts two MCP servers (agent-a and agent-b) on different ports without
OAuth or Firestore.  Patches agent_registry.discover() to return a fake
registry, and patches run_task's subprocess call to return a canned response.
Then verifies that agent-a can delegate a task to agent-b via the full MCP
Streamable HTTP handshake (initialize → initialized → tools/call).
"""

import asyncio
import json
import os
import sys
import time
import threading
import unittest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so server / oauth can be imported
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))


def _make_server_app(agent_name: str, port: int):
    """Build a Starlette MCP app for a given agent name (no OAuth)."""
    # We need isolated module-level state per server, so we construct
    # the FastMCP + tools inline rather than importing the shared `mcp`
    # singleton from server.py.
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    fmcp = FastMCP(
        f"{agent_name}-mcp",
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    @fmcp.tool()
    def run_task(prompt: str) -> str:
        """Fake run_task that echoes back the prompt with the agent name."""
        return f"[{agent_name}] received: {prompt}"

    @fmcp.tool()
    def list_peers() -> str:
        return json.dumps([])

    return fmcp


def _start_server(app_fmcp, port: int, ready_event: threading.Event):
    """Start a uvicorn server in a background thread."""
    import uvicorn

    starlette_app = app_fmcp.streamable_http_app()

    config = uvicorn.Config(
        starlette_app, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)

    # Signal readiness once the server is serving
    original_startup = server.startup

    async def patched_startup(sockets=None):
        await original_startup(sockets)
        ready_event.set()

    server.startup = patched_startup

    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.serve())


class TestA2ACommunication(unittest.TestCase):
    """Test the A2A delegate_task flow end-to-end."""

    SERVER_B_PORT = 19876  # peer server
    SERVER_B_URL = f"http://127.0.0.1:{SERVER_B_PORT}"

    @classmethod
    def setUpClass(cls):
        """Start agent-b's MCP server in a background thread."""
        cls.fmcp_b = _make_server_app("agent-b", cls.SERVER_B_PORT)
        cls.ready = threading.Event()
        cls.thread = threading.Thread(
            target=_start_server,
            args=(cls.fmcp_b, cls.SERVER_B_PORT, cls.ready),
            daemon=True,
        )
        cls.thread.start()
        if not cls.ready.wait(timeout=10):
            raise RuntimeError("agent-b server did not start in time")

    # ---- Unit tests for helpers ----

    def test_mint_peer_token_returns_none_without_oauth(self):
        """Without OAuth config, _mint_peer_token should return None."""
        # server.py's oauth_config is None in this test env (no env vars set)
        from server import _mint_peer_token

        token = _mint_peer_token("http://example.com")
        self.assertIsNone(token)

    def test_mint_peer_token_with_oauth(self):
        """With OAuth config, _mint_peer_token should return a valid JWT."""
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        import jwt as pyjwt

        # Generate a test RSA key
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )

        # Temporarily patch oauth_config
        import server

        fake_config = MagicMock()
        fake_config.client_id = "test-client"
        fake_config.signing_key = private_key

        original = server.oauth_config
        try:
            server.oauth_config = fake_config
            token = server._mint_peer_token("http://peer.example.com")
            self.assertIsNotNone(token)

            # Decode and verify claims
            claims = pyjwt.decode(
                token,
                private_key.public_key(),
                algorithms=["RS256"],
                audience="http://peer.example.com",
            )
            self.assertEqual(claims["iss"], "http://peer.example.com")
            self.assertEqual(claims["aud"], "http://peer.example.com")
            self.assertEqual(claims["sub"], "test-client")
        finally:
            server.oauth_config = original

    def test_list_peers_filters_self(self):
        """list_peers should exclude the calling agent from results."""
        import server

        fake_peers = [
            {"name": "gcloud-operator", "service_url": "http://a"},
            {"name": "firestore-agent", "service_url": "http://b"},
        ]
        with patch("server.AGENT_NAME", "gcloud-operator"):
            # Need to patch at call time since list_peers reads AGENT_NAME
            original_name = server.AGENT_NAME
            server.AGENT_NAME = "gcloud-operator"
            try:
                with patch("agent_registry.list_peers", return_value=fake_peers):
                    result = json.loads(server.list_peers())
                    names = [p["name"] for p in result]
                    self.assertNotIn("gcloud-operator", names)
                    self.assertIn("firestore-agent", names)
            finally:
                server.AGENT_NAME = original_name

    # ---- Integration test: _call_peer_tool ----

    def test_call_peer_tool_full_handshake(self):
        """_call_peer_tool should complete the MCP handshake and get a response."""
        from server import _call_peer_tool

        result = asyncio.get_event_loop().run_until_complete(
            _call_peer_tool(
                peer_url=self.SERVER_B_URL,
                token=None,  # no OAuth in test
                tool_name="run_task",
                arguments={"prompt": "hello from test"},
                timeout=30,
            )
        )
        self.assertIn("[agent-b]", result)
        self.assertIn("hello from test", result)

    # ---- Integration test: delegate_task ----

    def test_delegate_task_self_rejection(self):
        """delegate_task should reject self-delegation."""
        import server

        original_name = server.AGENT_NAME
        server.AGENT_NAME = "test-agent"
        try:
            with self.assertRaises(ValueError) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    server.delegate_task("test-agent", "do something")
                )
            self.assertIn("Cannot delegate to self", str(ctx.exception))
        finally:
            server.AGENT_NAME = original_name

    def test_delegate_task_peer_not_found(self):
        """delegate_task should raise ValueError for unknown peers."""
        import server

        with patch("agent_registry.discover", return_value=[]):
            with self.assertRaises(ValueError) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    server.delegate_task("nonexistent-agent", "do something")
                )
            self.assertIn("not found or offline", str(ctx.exception))

    def test_delegate_task_end_to_end(self):
        """delegate_task should discover a peer and get a response via MCP."""
        import server

        fake_registry = [
            {
                "name": "agent-b",
                "service_url": self.SERVER_B_URL,
                "capabilities": ["testing"],
                "description": "Test agent B",
                "status": "online",
            },
        ]

        original_name = server.AGENT_NAME
        server.AGENT_NAME = "agent-a"
        try:
            with patch("agent_registry.discover", return_value=fake_registry):
                result = asyncio.get_event_loop().run_until_complete(
                    server.delegate_task("agent-b", "ping from agent-a")
                )
                self.assertIn("[agent-b]", result)
                self.assertIn("ping from agent-a", result)
        finally:
            server.AGENT_NAME = original_name


if __name__ == "__main__":
    unittest.main()
