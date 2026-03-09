"""Agent discovery via Firestore registry and A2A Agent Cards."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


async def fetch_agent_card(url: str) -> dict | None:
    """Fetch an A2A Agent Card from a remote agent."""
    card_url = f"{url.rstrip('/')}/.well-known/agent.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(card_url)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        print(f"Failed to fetch agent card from {card_url}: {exc}", file=sys.stderr)
        return None


def discover_peers(config: AppConfig, exclude_self: bool = True) -> list[dict]:
    """Discover peers from the Firestore registry.

    Optionally filters out the calling agent.
    """
    from agent_runner.registry.firestore import discover

    peers = discover(project=config.gcp.project)
    if exclude_self:
        peers = [p for p in peers if p.get("name") != config.agent.name]
    return peers


def resolve_remote_subagent(config: AppConfig, name: str) -> str | None:
    """Resolve a remote subagent URL from config or registry.

    Returns the service URL or None if not found.
    """
    sub_config = config.subagents.get(name)
    if sub_config is None:
        return None

    if sub_config.url:
        return sub_config.url

    if sub_config.discovery == "registry":
        from agent_runner.registry.firestore import discover

        peers = discover(project=config.gcp.project)
        for peer in peers:
            if peer.get("name") == name:
                return peer.get("service_url")

    return None
