"""Pub/Sub capability announcements."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

PUBSUB_TOPIC = "agent-capabilities"


def publish_capability(
    project: str,
    agent_name: str,
    service_url: str,
    capabilities: list[str],
    description: str,
):
    """Publish a capability announcement to the shared Pub/Sub topic (non-fatal)."""
    try:
        from google.cloud.pubsub_v1 import PublisherClient

        publisher = PublisherClient()
        topic_path = f"projects/{project}/topics/{PUBSUB_TOPIC}"

        message = json.dumps({
            "event": "agent_online",
            "agent": agent_name,
            "service_url": service_url,
            "capabilities": capabilities,
            "description": description,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        publisher.publish(topic_path, message.encode())
        print(f"Published capability announcement for '{agent_name}'", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to publish to Pub/Sub: {exc}", file=sys.stderr)
