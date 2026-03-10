"""Bearer token authentication middleware."""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_runner.auth.oauth import OAuthConfig

# Paths that bypass authentication
OPEN_PATHS = frozenset({
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/.well-known/jwks.json",
    "/.well-known/agent.json",
    "/authorize",
    "/token",
})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT bearer tokens on all requests except OPEN_PATHS."""

    def __init__(self, app, oauth_config: OAuthConfig | None = None):
        super().__init__(app)
        # When OAuth is configured locally, use the public key directly.
        # Avoids self-referential JWKS HTTP fetch which deadlocks at concurrency=1.
        if oauth_config:
            self._local_public_key = oauth_config.signing_key.public_key()
            self._jwks_client = None
        else:
            self._local_public_key = None
            jwks_uri = os.environ.get("OAUTH2_JWKS_URI", "")
            self._jwks_client = PyJWKClient(jwks_uri, cache_keys=True) if jwks_uri else None

        # Audience/issuer: use explicit env vars if set, otherwise derive
        # dynamically from the request Host header at dispatch time.
        self._explicit_audience = os.environ.get("OAUTH2_AUDIENCE") or None
        self._explicit_issuer = os.environ.get("OAUTH2_ISSUER") or None

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        # No auth configured: pass through (local dev mode)
        if self._local_public_key is None and self._jwks_client is None:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth[len("Bearer "):]

        # Derive audience/issuer from request if not explicitly configured,
        # so tokens minted with the Host-derived base URL are accepted.
        request_base = str(request.base_url).rstrip("/")
        audience = self._explicit_audience or request_base
        issuer = self._explicit_issuer or request_base

        try:
            if self._local_public_key:
                jwt.decode(
                    token,
                    self._local_public_key,
                    algorithms=["RS256"],
                    audience=audience,
                    issuer=issuer,
                )
            else:
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
                jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=audience,
                    issuer=issuer,
                )
        except jwt.exceptions.PyJWTError as exc:
            return JSONResponse(
                {"error": "forbidden", "detail": str(exc)}, status_code=403,
            )

        return await call_next(request)
