"""MCP server exposing a Claude agent over Streamable HTTP with OAuth 2.1 auth."""

import json
import os
import subprocess

import jwt
from jwt import PyJWKClient
from cryptography.hazmat.primitives import serialization
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from oauth import OAuthConfig, create_oauth_app

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080")
PORT = int(os.environ.get("PORT", "8080"))
AGENT_NAME = os.environ.get("AGENT_NAME", "gcloud-operator")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "300"))

OPEN_PATHS = frozenset({
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/.well-known/jwks.json",
    "/authorize",
    "/token",
})


def _load_oauth_config() -> OAuthConfig | None:
    creds = os.environ.get("OAUTH_CLIENT_CREDENTIALS", "")
    key_pem = os.environ.get("OAUTH_SIGNING_KEY", "")
    if not creds or not key_pem:
        return None

    client_id, _, client_secret = creds.partition(":")
    signing_key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    return OAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        signing_key=signing_key,
        public_url=PUBLIC_URL,
    )


oauth_config = _load_oauth_config()

# When OAuth is configured locally, use the public key directly (avoids self-referential
# JWKS HTTP fetch which deadlocks with concurrency=1 on Cloud Run).
if oauth_config:
    _local_public_key = oauth_config.signing_key.public_key()
    _jwks_client = None
else:
    _local_public_key = None
    _jwks_uri = os.environ.get("OAUTH2_JWKS_URI", "")
    _jwks_client = PyJWKClient(_jwks_uri, cache_keys=True) if _jwks_uri else None

OAUTH2_AUDIENCE = os.environ.get("OAUTH2_AUDIENCE") or (PUBLIC_URL if oauth_config else None)
OAUTH2_ISSUER = os.environ.get("OAUTH2_ISSUER") or (PUBLIC_URL if oauth_config else None)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        if _local_public_key is None and _jwks_client is None:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth[len("Bearer "):]
        try:
            if _local_public_key:
                jwt.decode(
                    token,
                    _local_public_key,
                    algorithms=["RS256"],
                    audience=OAUTH2_AUDIENCE,
                    issuer=OAUTH2_ISSUER,
                    options={
                        "verify_aud": bool(OAUTH2_AUDIENCE),
                        "verify_iss": bool(OAUTH2_ISSUER),
                    },
                )
            else:
                signing_key = _jwks_client.get_signing_key_from_jwt(token)
                jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=OAUTH2_AUDIENCE,
                    issuer=OAUTH2_ISSUER,
                    options={
                        "verify_aud": bool(OAUTH2_AUDIENCE),
                        "verify_iss": bool(OAUTH2_ISSUER),
                    },
                )
        except jwt.exceptions.PyJWTError as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

        return await call_next(request)


from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    f"{AGENT_NAME}-mcp",
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def run_task(prompt: str) -> str:
    """Run a task using the configured Claude agent."""
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--agent", AGENT_NAME,
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=AGENT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "Agent task failed")
    return result.stdout


@mcp.tool()
def list_peers() -> str:
    """List other agents available in this project for potential task delegation."""
    try:
        from agent_registry import list_peers as _list_peers

        peers = _list_peers()
        return json.dumps(peers, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


if __name__ == "__main__":
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn

    if oauth_config:
        mcp_app = mcp.streamable_http_app()
        session_manager = mcp._session_manager

        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with session_manager.run():
                yield

        oauth_app = create_oauth_app(oauth_config)
        all_routes = list(oauth_app.routes) + [Mount("/", app=mcp_app)]
        app = Starlette(routes=all_routes, lifespan=lifespan)
    else:
        app = mcp.streamable_http_app()

    app.add_middleware(BearerAuthMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
