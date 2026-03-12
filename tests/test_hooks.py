"""Tests for hook registration, audit hook, and reflection hook."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

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


# ---- Reflection hook tests ----


class TestReflectionStopHook:
    """Tests for the two-phase reflection stop hook."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset ContextVar state before each test."""
        from agent_runner.hooks.reflection import _session_ctx

        token = _session_ctx.set({})
        yield
        _session_ctx.reset(token)

    async def test_phase1_returns_system_message(self):
        from agent_runner.hooks.reflection import reflection_stop_hook

        result = await reflection_stop_hook({}, "tool-1", None)

        assert result is not None
        assert "systemMessage" in result
        assert "reflect" in result["systemMessage"].lower()

    async def test_phase1_sets_session_id(self):
        from agent_runner.hooks.reflection import _session_ctx, reflection_stop_hook

        await reflection_stop_hook({}, "tool-1", None)

        state = _session_ctx.get()
        assert "session_id" in state
        assert "start_time" in state

    async def test_phase2_captures_reflection_and_persists(self):
        from agent_runner.hooks.reflection import _session_ctx, reflection_stop_hook

        # Phase 1
        await reflection_stop_hook({}, "tool-1", None)
        session_id = _session_ctx.get()["session_id"]
        start_time = _session_ctx.get()["start_time"]

        # Phase 2 — provide reflection content
        input_data = {
            "content": [
                {"type": "text", "text": "I learned about testing."},
            ]
        }

        with patch("agent_runner.hooks.reflection._persist_reflection") as mock_persist:
            result = await reflection_stop_hook(input_data, "tool-2", None)

        assert result is None
        mock_persist.assert_called_once_with(
            session_id, "I learned about testing.", start_time,
        )

    async def test_phase2_resets_state(self):
        from agent_runner.hooks.reflection import _session_ctx, reflection_stop_hook

        await reflection_stop_hook({}, "tool-1", None)

        with patch("agent_runner.hooks.reflection._persist_reflection"):
            await reflection_stop_hook({"content": []}, "tool-2", None)

        assert _session_ctx.get() == {}

    async def test_phase2_handles_empty_content(self):
        from agent_runner.hooks.reflection import _session_ctx, reflection_stop_hook

        await reflection_stop_hook({}, "tool-1", None)
        start_time = _session_ctx.get()["start_time"]

        with patch("agent_runner.hooks.reflection._persist_reflection") as mock_persist:
            await reflection_stop_hook({}, "tool-2", None)

        # session_id is a UUID string, just verify it was called with empty learnings
        assert mock_persist.call_count == 1
        assert mock_persist.call_args[0][1] == ""
        assert mock_persist.call_args[0][2] == start_time

    async def test_concurrent_sessions_isolated(self):
        """Two concurrent tasks get independent session state."""
        from agent_runner.hooks.reflection import reflection_stop_hook

        session_ids = []

        async def run_session(label: str):
            with patch("agent_runner.hooks.reflection._persist_reflection") as mock_persist:
                # Phase 1
                result = await reflection_stop_hook({}, f"tool-{label}-1", None)
                assert result is not None

                # Phase 2
                input_data = {"content": [{"type": "text", "text": f"Learnings from {label}"}]}
                await reflection_stop_hook(input_data, f"tool-{label}-2", None)

                sid = mock_persist.call_args[0][0]
                learnings = mock_persist.call_args[0][1]
                session_ids.append(sid)
                assert learnings == f"Learnings from {label}"

        # Run two sessions concurrently — each asyncio.Task gets its own ContextVar copy
        await asyncio.gather(
            asyncio.create_task(run_session("A")),
            asyncio.create_task(run_session("B")),
        )

        # Each session should have a unique session_id
        assert len(session_ids) == 2
        assert session_ids[0] != session_ids[1]


class TestPersistReflection:
    def test_persist_writes_to_firestore(self):
        from agent_runner.hooks.reflection import _persist_reflection

        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc

        with patch("google.cloud.firestore.Client", return_value=mock_db), \
             patch("agent_runner.config.load_config") as mock_cfg:
            mock_cfg.return_value = load_config(path="/nonexistent.yaml")
            _persist_reflection("session-123", "learnings text", 1000.0)

        mock_collection.document.assert_called_once_with("session-123")
        doc_data = mock_doc.set.call_args[0][0]
        assert doc_data["session_id"] == "session-123"
        assert doc_data["learnings"] == "learnings text"
        assert "timestamp" in doc_data

    def test_persist_handles_failure(self, capsys):
        from agent_runner.hooks.reflection import _persist_reflection

        with patch(
            "google.cloud.firestore.Client",
            side_effect=Exception("firestore down"),
        ), patch("agent_runner.config.load_config") as mock_cfg:
            mock_cfg.return_value = load_config(path="/nonexistent.yaml")
            # Should not raise
            _persist_reflection("s-1", "text", 1000.0)

        captured = capsys.readouterr()
        assert "Failed to persist reflection" in captured.err
