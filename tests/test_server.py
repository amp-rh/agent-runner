"""Tests for OAuth endpoints and auth middleware."""

import base64
import hashlib
import secrets

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_runner.auth.middleware import BearerAuthMiddleware, OPEN_PATHS
from agent_runner.auth.oauth import OAuthConfig, create_oauth_app


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oauth_config(signing_key):
    return OAuthConfig(
        client_id="test-client",
        client_secret="test-secret",
        signing_key=signing_key,
        public_url="http://localhost:8080",
    )


@pytest.fixture
def oauth_client(oauth_config):
    app = create_oauth_app(oauth_config)
    return TestClient(app)


def test_metadata_endpoint(oauth_client):
    resp = oauth_client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    data = resp.json()
    assert data["issuer"] == "http://testserver"
    assert "authorization_endpoint" in data
    assert "token_endpoint" in data
    assert "S256" in data["code_challenge_methods_supported"]


def test_jwks_endpoint(oauth_client):
    resp = oauth_client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert len(data["keys"]) == 1
    assert data["keys"][0]["alg"] == "RS256"


def test_authorize_flow(oauth_client):
    """Authorization endpoint returns redirect with code."""
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    resp = oauth_client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "test-state",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "code=" in location
    assert "state=test-state" in location


def test_authorize_rejects_invalid_client(oauth_client):
    resp = oauth_client.get(
        "/authorize",
        params={
            "client_id": "wrong-client",
            "redirect_uri": "http://localhost/callback",
            "code_challenge": "abc",
            "code_challenge_method": "S256",
            "state": "s",
            "response_type": "code",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_full_oauth_flow(oauth_client, oauth_config):
    """Full PKCE flow: authorize -> token."""
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    # Get auth code
    resp = oauth_client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "s",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    location = resp.headers["location"]
    code = location.split("code=")[1].split("&")[0]

    # Exchange for token
    resp = oauth_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": "test-client",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost/callback",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Validate the access token
    pub_key = oauth_config.signing_key.public_key()
    claims = jwt.decode(
        data["access_token"],
        pub_key,
        algorithms=["RS256"],
        audience="http://testserver",
    )
    assert claims["sub"] == "test-client"


def test_protected_resource_endpoint(oauth_client):
    resp = oauth_client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    data = resp.json()
    assert data["resource"] == "http://testserver/mcp"


def test_agent_card_in_open_paths():
    """/.well-known/agent.json must be in OPEN_PATHS to avoid 401 (issue #17)."""
    assert "/.well-known/agent.json" in OPEN_PATHS


def test_open_paths_bypass_auth(oauth_config):
    """All OPEN_PATHS return 200 without auth when using the middleware."""
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    routes = [Route(path, ok) for path in OPEN_PATHS]
    inner_app = Starlette(routes=routes)
    inner_app.add_middleware(BearerAuthMiddleware, oauth_config=oauth_config)
    client = TestClient(inner_app, raise_server_exceptions=False)

    for path in OPEN_PATHS:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}, expected 200"


def test_auth_required_for_non_open_paths(oauth_config):
    """Non-open paths return 401 without a bearer token."""
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    inner_app = Starlette(routes=[Route("/mcp", ok)])
    inner_app.add_middleware(BearerAuthMiddleware, oauth_config=oauth_config)
    client = TestClient(inner_app, raise_server_exceptions=False)

    resp = client.get("/mcp")
    assert resp.status_code == 401
