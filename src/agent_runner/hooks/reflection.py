"""Stop hook: persist session learnings to Firestore."""

from __future__ import annotations

import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

REFLECTION_PROMPT = (
    "Before ending, reflect on this session. Summarize: "
    "(1) what you accomplished, (2) key learnings or patterns discovered, "
    "(3) tools used and their effectiveness. Be concise."
)

# Per-task session state via ContextVar (isolated across concurrent asyncio tasks)
_session_ctx: ContextVar[dict] = ContextVar("_session_ctx", default={})


async def reflection_stop_hook(input_data, tool_use_id, context):
    """Two-phase stop hook.

    First call: inject a system message asking the agent to reflect.
    Second call: capture the reflection output and persist to Firestore.
    """
    state = _session_ctx.get()
    session_id = state.get("session_id")

    if session_id is None:
        # Phase 1: first stop event. Ask for reflection.
        _session_ctx.set({
            "session_id": str(uuid.uuid4()),
            "start_time": time.time(),
        })
        return {
            "systemMessage": REFLECTION_PROMPT,
        }

    # Phase 2: agent has reflected. Capture and store.
    reflection_text = ""
    if isinstance(input_data, dict):
        # The stop event may carry the last assistant message
        content = input_data.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    reflection_text += block.get("text", "")

    _persist_reflection(session_id, reflection_text, state.get("start_time", time.time()))

    # Reset state for next session
    _session_ctx.set({})
    return None


def _persist_reflection(session_id: str, learnings: str, start_time: float):
    """Write session learnings to Firestore (best-effort)."""
    try:
        from google.cloud.firestore import Client

        from agent_runner.config import load_config

        config = load_config()
        hook_cfg = config.hooks.reflection
        db = Client(project=hook_cfg.firestore_project, database=hook_cfg.firestore_database)

        doc = {
            "session_id": session_id,
            "agent_name": config.agent.name,
            "timestamp": datetime.now(timezone.utc),
            "learnings": learnings,
            "duration_seconds": time.time() - start_time,
            "model": config.agent.model,
        }

        db.collection(hook_cfg.collection).document(session_id).set(doc)
        print(f"Reflection saved for session {session_id}", file=sys.stderr)
    except Exception as exc:
        print(f"Failed to persist reflection: {exc}", file=sys.stderr)
