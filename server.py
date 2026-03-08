"""MCP server exposing a Claude agent over Streamable HTTP with OAuth 2.1 auth."""

import json
import os
import secrets
import subprocess
import time

import httpx
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
        peers = [p for p in peers if p.get("name") != AGENT_NAME]
        return json.dumps(peers, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _mint_peer_token(peer_url: str) -> str | None:
    """Mint a JWT access token accepted by a peer agent's BearerAuthMiddleware.

    All agents share the same RSA signing key, so a token signed here will
    validate at the peer.  The ``iss`` and ``aud`` claims must match the
    peer's PUBLIC_URL (which equals its service_url in the registry).
    """
    if oauth_config is None:
        return None

    now = int(time.time())
    payload = {
        "iss": peer_url,
        "aud": peer_url,
        "sub": oauth_config.client_id,
        "exp": now + 300,
        "iat": now,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(
        payload,
        oauth_config.signing_key,
        algorithm="RS256",
        headers={"kid": "mcp-signing-key"},
    )


async def _call_peer_tool(
    peer_url: str,
    token: str | None,
    tool_name: str,
    arguments: dict,
    timeout: float,
) -> str:
    """Call an MCP tool on a peer agent via Streamable HTTP (JSON-RPC).

    Performs the full MCP handshake:
      1. ``initialize`` — server returns session ID
      2. ``notifications/initialized`` — confirm ready
      3. ``tools/call`` — invoke the tool
    """
    url = peer_url.rstrip("/") + "/mcp"
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Step 1: initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "clientInfo": {"name": AGENT_NAME, "version": "1.0"},
                "capabilities": {},
            },
        }
        resp = await client.post(url, json=init_req, headers=headers)
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        sess_headers = {**headers}
        if session_id:
            sess_headers["Mcp-Session-Id"] = session_id

        # Step 2: send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        await client.post(url, json=notif, headers=sess_headers)

        # Step 3: call the tool
        tool_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        resp = await client.post(url, json=tool_req, headers=sess_headers)
        resp.raise_for_status()

    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"Peer error: {result['error']}")

    content = result.get("result", {}).get("content", [])
    texts = [c["text"] for c in content if c.get("type") == "text"]
    return "\n".join(texts) if texts else str(result.get("result"))


@mcp.tool()
async def delegate_task(peer_name: str, prompt: str) -> str:
    """Delegate a task to a peer agent by name.

    Discovers the peer via the Firestore registry, mints a JWT for
    authentication, and invokes the peer's ``run_task`` MCP tool.
    """
    if peer_name == AGENT_NAME:
        raise ValueError(
            f"Cannot delegate to self ('{AGENT_NAME}'). "
            "Use run_task for local execution."
        )

    from agent_registry import discover

    peers = discover()
    peer = next((p for p in peers if p["name"] == peer_name), None)
    if not peer:
        available = [p["name"] for p in peers if p["name"] != AGENT_NAME]
        raise ValueError(
            f"Peer '{peer_name}' not found or offline. "
            f"Available peers: {available}"
        )

    peer_url = peer["service_url"]
    token = _mint_peer_token(peer_url)

    try:
        result = await _call_peer_tool(
            peer_url=peer_url,
            token=token,
            tool_name="run_task",
            arguments={"prompt": prompt},
            timeout=AGENT_TIMEOUT + 30,
        )
        return result
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Peer '{peer_name}' returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:500]}"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Peer '{peer_name}' at {peer_url} is unreachable: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Failed to delegate to '{peer_name}': {exc}"
        ) from exc


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
