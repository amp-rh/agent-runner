"""Tests for hook registration and audit hook."""

import json

import pytest

from agent_runner.config import load_config


def test_build_hooks_default():
    """Default config enables both reflection and audit hooks."""
    from agent_runner.hooks.registry import build_hooks

    config = load_config(path="/nonexistent.yaml")
    hooks = build_hooks(config)

    assert hooks is not None
    assert "Stop" in hooks
    assert "PreToolUse" in hooks


def test_build_hooks_disabled(tmp_path):
    """Hooks can be disabled via config."""
    import yaml

    cfg = {
        "hooks": {
            "reflection": {"enabled": False},
            "audit": {"enabled": False},
        }
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    from agent_runner.hooks.registry import build_hooks

    config = load_config(path=cfg_file)
    hooks = build_hooks(config)

    assert hooks is None


@pytest.mark.asyncio
async def test_audit_hook_logs(capsys):
    """Audit hook logs tool invocations to stderr."""
    from agent_runner.hooks.audit import audit_pre_tool_hook

    result = await audit_pre_tool_hook(
        {"tool_name": "Read", "input": {"path": "/tmp/test"}},
        "test-tool-use-id",
        None,
    )

    assert result is None
    captured = capsys.readouterr()
    log_line = captured.err.strip()
    entry = json.loads(log_line)
    assert entry["event"] == "tool_use"
    assert entry["tool"] == "Read"
    assert entry["tool_use_id"] == "test-tool-use-id"
