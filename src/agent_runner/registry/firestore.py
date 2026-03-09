"""Firestore agent registry CRUD operations."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

DEFAULT_DATABASE = "agents"
REGISTRY_COLLECTION = "registry"


def _firestore_client(project: str, database: str = DEFAULT_DATABASE):
    from google.cloud.firestore import Client

    return Client(project=project, database=database)


def advertise(
    agent_name: str,
    service_url: str,
    capabilities: list[str],
    description: str,
    project: str,
):
    """Upsert agent entry in the Firestore registry (non-fatal on failure)."""
    now = datetime.now(timezone.utc)
    registry_data = {
        "name": agent_name,
        "service_url": service_url,
        "capabilities": capabilities,
        "description": description,
        "status": "online",
        "last_heartbeat": now,
        "project": project,
    }

    try:
        db = _firestore_client(project)
        db.collection(REGISTRY_COLLECTION).document(agent_name).set(registry_data, merge=True)
        print(f"Registry updated for agent '{agent_name}'", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to update registry: {exc}", file=sys.stderr)


def discover(capability: str | None = None, project: str = "claude-connectors") -> list[dict]:
    """Query the Firestore registry for available agents."""
    db = _firestore_client(project)
    query = db.collection(REGISTRY_COLLECTION).where("status", "==", "online")

    results = []
    for doc in query.stream():
        data = doc.to_dict()
        if capability and capability not in data.get("capabilities", []):
            continue
        results.append(data)

    return results


def list_peers(project: str = "claude-connectors") -> list[dict]:
    """Return all registered agents."""
    return discover(project=project)
