"""A2A client for calling remote agents."""

from __future__ import annotations

import secrets
import time

import httpx
import jwt

from agent_runner.auth.oauth import OAuthConfig


def mint_peer_token(oauth_config: OAuthConfig, peer_url: str) -> str | None:
    """Mint a JWT accepted by a peer agent's BearerAuthMiddleware.

    All agents share the same RSA signing key. The iss and aud claims
    must match the peer's PUBLIC_URL for validation to succeed.
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


async def call_remote_agent(
    peer_url: str,
    prompt: str,
    token: str | None = None,
    timeout: float = 330,
) -> str:
    """Call a remote agent via A2A protocol.

    Uses the a2a-sdk client to send a task to the remote agent
    and collect the result.
    """
    from a2a.client import A2AClient

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
        agent_card_url = f"{peer_url.rstrip('/')}/.well-known/agent.json"
        client = await A2AClient.get_client_from_agent_card_url(http, agent_card_url)

        # Send task with the prompt
        response = await client.send_task({
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
            }
        })

        # Extract result text from the response
        if hasattr(response, "result") and response.result:
            task = response.result
            if hasattr(task, "artifacts") and task.artifacts:
                texts = []
                for artifact in task.artifacts:
                    for part in artifact.parts:
                        if hasattr(part, "text"):
                            texts.append(part.text)
                return "\n".join(texts)

        return str(response)
