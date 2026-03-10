"""Build hook configuration for the Claude Agent SDK."""

from __future__ import annotations

from typing import TYPE_CHECKING

from claude_agent_sdk import HookMatcher

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


def build_hooks(config: AppConfig) -> dict | None:
    """Build hooks dict for ClaudeAgentOptions from config."""
    hooks: dict[str, list[HookMatcher]] = {}

    if config.hooks.reflection.enabled:
        from agent_runner.hooks.reflection import reflection_stop_hook

        hooks.setdefault("Stop", []).append(
            HookMatcher(hooks=[reflection_stop_hook])
        )

    if config.hooks.audit.enabled:
        from agent_runner.hooks.audit import audit_pre_tool_hook

        hooks.setdefault("PreToolUse", []).append(
            HookMatcher(hooks=[audit_pre_tool_hook])
        )

    return hooks or None
