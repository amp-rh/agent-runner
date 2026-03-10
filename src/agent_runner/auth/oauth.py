"""OAuth 2.1 authorization server for MCP connector compatibility.

Implements RFC 8414 metadata, authorization code flow with PKCE, and JWKS.
Single-tenant: auto-approves authorization for valid client_id.
Stateless auth codes: encoded as signed JWTs to survive scale-to-zero.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    signing_key: rsa.RSAPrivateKey
    public_url: str
    token_ttl: int = 3600


def _public_jwk(key: rsa.RSAPrivateKey) -> dict:
    """Build a JWK dict from an RSA private key."""
    pub = key.public_key()
    nums = pub.public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    byte_len = (pub.key_size + 7) // 8
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "mcp-signing-key",
        "n": _b64url(nums.n, byte_len),
        "e": _b64url(nums.e, 3),
    }


def _encode_jwt(payload: dict, key: rsa.RSAPrivateKey) -> str:
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "mcp-signing-key"})


def load_oauth_config(public_url: str) -> OAuthConfig | None:
    """Load OAuth config from environment variables. Returns None if not configured."""
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
        public_url=public_url,
    )


def create_oauth_app(config: OAuthConfig) -> Starlette:
    """Create a Starlette app with OAuth 2.1 endpoints."""
    jwk = _public_jwk(config.signing_key)

    async def metadata(request: Request) -> JSONResponse:
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post", "client_secret_basic",
            ],
            "code_challenge_methods_supported": ["S256"],
        })

    async def jwks(request: Request) -> JSONResponse:
        return JSONResponse({"keys": [jwk]})

    async def authorize(request: Request) -> RedirectResponse | JSONResponse:
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "")
        state = params.get("state", "")
        response_type = params.get("response_type", "")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if client_id != config.client_id:
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        if code_challenge_method != "S256":
            return JSONResponse(
                {"error": "invalid_request", "error_description": "S256 required"},
                status_code=400,
            )
        if not redirect_uri or not code_challenge:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        code_payload = {
            "type": "auth_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "exp": int(time.time()) + 120,
            "jti": secrets.token_urlsafe(16),
        }
        code = _encode_jwt(code_payload, config.signing_key)
        qs = urlencode({"code": code, "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = form.get("grant_type", "")
        code = form.get("code", "")
        code_verifier = form.get("code_verifier", "")
        client_id = form.get("client_id", "")
        client_secret = form.get("client_secret", "")
        redirect_uri = form.get("redirect_uri", "")

        if not client_id or not client_secret:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Basic "):
                import binascii
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode()
                    client_id, _, client_secret = decoded.partition(":")
                except (binascii.Error, UnicodeDecodeError):
                    pass

        if grant_type != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        if client_id != config.client_id or client_secret != config.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        try:
            pub_key = config.signing_key.public_key()
            claims = jwt.decode(code, pub_key, algorithms=["RS256"])
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "code expired"},
                status_code=400,
            )
        except jwt.PyJWTError:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if claims.get("type") != "auth_code":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if claims.get("client_id") != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if claims.get("redirect_uri") != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        digest = hashlib.sha256(code_verifier.encode()).digest()
        expected_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if expected_challenge != claims.get("code_challenge"):
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE verification failed"},
                status_code=400,
            )

        now = int(time.time())
        base = str(request.base_url).rstrip("/")
        access_token = _encode_jwt({
            "iss": base,
            "sub": client_id,
            "aud": base,
            "exp": now + config.token_ttl,
            "iat": now,
            "jti": secrets.token_urlsafe(16),
        }, config.signing_key)

        return JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": config.token_ttl,
        })

    async def protected_resource(request: Request) -> JSONResponse:
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
        })

    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/.well-known/oauth-protected-resource", protected_resource),
        Route("/.well-known/jwks.json", jwks),
        Route("/authorize", authorize),
        Route("/token", token, methods=["POST"]),
    ])
