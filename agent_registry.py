"""Agent discovery via Pub/Sub and Firestore registry.

On startup (server mode), publishes a capability announcement to a shared
Pub/Sub topic and upserts the agent's entry in the Firestore registry.
Other agents query the registry to discover peers.
"""

import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_PROJECT = os.environ.get("GCP_PROJECT", "claude-connectors")
DEFAULT_DATABASE = "agents"
REGISTRY_COLLECTION = "registry"
PUBSUB_TOPIC = "agent-capabilities"


def _firestore_client(project: str, database: str = DEFAULT_DATABASE):
    from google.cloud.firestore import Client

    return Client(project=project, database=database)


def _pubsub_publisher(project: str):
    from google.cloud.pubsub_v1 import PublisherClient

    return PublisherClient(), f"projects/{project}/topics/{PUBSUB_TOPIC}"


def advertise(
    agent_name: str,
    service_url: str,
    capabilities: list[str],
    description: str,
    project: str = DEFAULT_PROJECT,
):
    """Publish capability announcement and upsert Firestore registry."""
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

    try:
        publisher, topic_path = _pubsub_publisher(project)
        message = json.dumps({
            "event": "agent_online",
            "agent": agent_name,
            "service_url": service_url,
            "capabilities": capabilities,
            "description": description,
            "timestamp": now.isoformat(),
        })
        publisher.publish(topic_path, message.encode())
        print(f"Published capability announcement for '{agent_name}'", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to publish to Pub/Sub: {exc}", file=sys.stderr)


def discover(capability: str | None = None, project: str = DEFAULT_PROJECT) -> list[dict]:
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


def list_peers(project: str = DEFAULT_PROJECT) -> list[dict]:
    """Return all registered agents with their URLs and capabilities."""
    return discover(project=project)


def main():
    agent_name = os.environ.get("AGENT_NAME", "gcloud-operator")
    service_url = os.environ.get("PUBLIC_URL", "http://localhost:8080")
    capabilities_str = os.environ.get("AGENT_CAPABILITIES", "")
    capabilities = [c.strip() for c in capabilities_str.split(",") if c.strip()]
    description = os.environ.get("AGENT_DESCRIPTION", f"{agent_name} agent")
    project = os.environ.get("GCP_PROJECT", DEFAULT_PROJECT)

    advertise(agent_name, service_url, capabilities, description, project)


if __name__ == "__main__":
    main()
