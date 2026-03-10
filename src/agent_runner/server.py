"""Starlette app composition: FastMCP + A2A + OAuth."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import Mount

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


class PublicURLMiddleware(BaseHTTPMiddleware):
    """Auto-derive public_url from the first non-localhost request Host header.

    On Cloud Run, the service URL is not known until the first request arrives.
    This middleware captures the Host header and updates config.server.public_url,
    then re-registers the agent with the correct URL.
    """

    def __init__(self, app, config: AppConfig):
        super().__init__(app)
        self._config = config
        self._resolved = False

    async def dispatch(self, request: Request, call_next):
        if not self._resolved and self._config.server.public_url == "http://localhost:8080":
            host = request.headers.get("host", "")
            if host and not host.startswith("localhost"):
                scheme = request.headers.get("x-forwarded-proto", "https")
                self._config.server.public_url = f"{scheme}://{host}"
                self._resolved = True
                _register_agent(self._config)
        return await call_next(request)


def create_app(config: AppConfig) -> Starlette:
    """Create the composed Starlette application."""
    from agent_runner.agent import AgentRunner
    from agent_runner.auth.middleware import BearerAuthMiddleware
    from agent_runner.auth.oauth import create_oauth_app, load_oauth_config
    from agent_runner.hooks.registry import build_hooks
    from agent_runner.mcp.server import create_mcp_server

    # Load OAuth config
    oauth_config = load_oauth_config(config.server.public_url)

    # Build hooks
    hooks = build_hooks(config)

    # Create agent runner
    agent_runner = AgentRunner(config, hooks=hooks)

    # Create MCP server
    mcp = create_mcp_server(config, agent_runner)
    mcp_app = mcp.streamable_http_app()
    session_manager = mcp._session_manager

    # Build routes
    routes = []

    # OAuth routes (if configured)
    if oauth_config:
        oauth_app = create_oauth_app(oauth_config)
        routes.extend(oauth_app.routes)

    # A2A routes (if enabled)
    if config.a2a.enabled:
        try:
            a2a_app = _build_a2a_app(config, agent_runner)
            if a2a_app:
                routes.append(Mount("/.well-known", app=a2a_app))
        except Exception as exc:
            print(f"A2A initialization failed (non-fatal): {exc}", file=sys.stderr)

    # MCP routes (catch-all, must be last)
    routes.append(Mount("/", app=mcp_app))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            _register_agent(config)
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(BearerAuthMiddleware, oauth_config=oauth_config)
    app.add_middleware(PublicURLMiddleware, config=config)

    return app


def _build_a2a_app(config, agent_runner):
    """Build the A2A Starlette sub-application."""
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore

    from agent_runner.a2a.card import build_agent_card
    from agent_runner.a2a.executor import ClaudeAgentExecutor

    card = build_agent_card(config)
    executor = ClaudeAgentExecutor(agent_runner)
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    a2a_app = A2AStarletteApplication(agent_card=card, http_handler=handler)
    return a2a_app.build()


def _register_agent(config):
    """Register agent in Firestore registry and publish to Pub/Sub (non-fatal)."""
    try:
        from agent_runner.registry.firestore import advertise
        from agent_runner.registry.pubsub import publish_capability

        # Extract capabilities from A2A skills
        capabilities = []
        for skill in config.a2a.skills:
            capabilities.extend(skill.tags)

        advertise(
            agent_name=config.agent.name,
            service_url=config.server.public_url,
            capabilities=capabilities,
            description=config.agent.description,
            project=config.gcp.project,
        )
        publish_capability(
            project=config.gcp.project,
            agent_name=config.agent.name,
            service_url=config.server.public_url,
            capabilities=capabilities,
            description=config.agent.description,
        )
    except Exception as exc:
        print(f"Agent registration failed (non-fatal): {exc}", file=sys.stderr)
