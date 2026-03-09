"""Pub/Sub background worker mode.

Subscribes to a Pub/Sub subscription, processes messages as agent tasks,
and writes results to Firestore or a response topic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


async def run_worker(config: AppConfig):
    """Start the Pub/Sub worker loop."""
    from agent_runner.agent import AgentRunner
    from agent_runner.hooks.registry import build_hooks

    subscription = config.invocation.pubsub_subscription
    if not subscription:
        print("No Pub/Sub subscription configured. Set invocation.pubsub_subscription.", file=sys.stderr)
        sys.exit(1)

    hooks = build_hooks(config)
    runner = AgentRunner(config, hooks=hooks)

    print(f"Starting Pub/Sub worker on subscription: {subscription}", file=sys.stderr)
    await _subscribe_loop(config, runner, subscription)


async def _subscribe_loop(config: AppConfig, runner, subscription: str):
    """Pull messages from Pub/Sub and process them."""
    from google.cloud.pubsub_v1 import SubscriberClient

    subscriber = SubscriberClient()
    sub_path = subscription
    if not sub_path.startswith("projects/"):
        sub_path = f"projects/{config.gcp.project}/subscriptions/{subscription}"

    def callback(message):
        try:
            data = json.loads(message.data.decode())
            prompt = data.get("prompt", "")
            task_id = data.get("task_id", message.message_id)

            if not prompt:
                print(f"Message {task_id} has no prompt, skipping", file=sys.stderr)
                message.ack()
                return

            print(f"Processing task {task_id}", file=sys.stderr)
            result = asyncio.run(runner.run(prompt))
            _store_result(config, task_id, prompt, result)
            message.ack()
            print(f"Task {task_id} completed", file=sys.stderr)
        except Exception as exc:
            print(f"Task processing failed: {exc}", file=sys.stderr)
            message.nack()

    future = subscriber.subscribe(sub_path, callback=callback)
    print(f"Worker listening on {sub_path}", file=sys.stderr)

    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()
        future.result()


def _store_result(config: AppConfig, task_id: str, prompt: str, result: str):
    """Store task result in Firestore (best-effort)."""
    try:
        from google.cloud.firestore import Client

        db = Client(project=config.gcp.project, database="agents")
        db.collection("task_results").document(task_id).set({
            "task_id": task_id,
            "agent_name": config.agent.name,
            "prompt": prompt,
            "result": result,
            "timestamp": datetime.now(timezone.utc),
            "status": "completed",
        })
    except Exception as exc:
        print(f"Failed to store result for task {task_id}: {exc}", file=sys.stderr)
