"""PreToolUse hook: log tool invocations for audit trail."""

from __future__ import annotations

import json
import sys
import time


async def audit_pre_tool_hook(input_data, tool_use_id, context):
    """Log every tool invocation with timestamp and tool name."""
    tool_name = "unknown"
    if isinstance(input_data, dict):
        tool_name = input_data.get("tool_name", input_data.get("name", "unknown"))

    entry = {
        "event": "tool_use",
        "tool": tool_name,
        "tool_use_id": tool_use_id,
        "timestamp": time.time(),
    }
    print(json.dumps(entry), file=sys.stderr)

    return None
