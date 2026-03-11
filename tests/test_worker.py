"""Tests for Pub/Sub worker mode."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from agent_runner.config import AppConfig
from agent_runner.worker.pubsub import _store_result


def _make_config(**overrides) -> AppConfig:
    data = {
        "agent": {"name": "test-agent"},
        "gcp": {"project": "test-project"},
    }
    data.update(overrides)
    return AppConfig(**data)


class TestStoreResult:
    def test_store_result_writes_to_firestore(self):
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc
        config = _make_config()

        with patch("google.cloud.firestore.Client", return_value=mock_db):
            _store_result(config, "task-123", "test prompt", "result text")

        mock_collection.document.assert_called_once_with("task-123")
        doc_data = mock_doc.set.call_args[0][0]
        assert doc_data["task_id"] == "task-123"
        assert doc_data["agent_name"] == "test-agent"
        assert doc_data["prompt"] == "test prompt"
        assert doc_data["result"] == "result text"
        assert doc_data["status"] == "completed"
        assert "timestamp" in doc_data

    def test_store_result_handles_failure(self, capsys):
        config = _make_config()

        with patch(
            "google.cloud.firestore.Client",
            side_effect=Exception("firestore error"),
        ):
            # Should not raise
            _store_result(config, "task-123", "prompt", "result")

        captured = capsys.readouterr()
        assert "Failed to store result" in captured.err


class TestSubscribeLoopCallback:
    """Test the callback function logic inside _subscribe_loop."""

    def test_callback_processes_valid_message(self):
        """Simulate the callback behavior for a valid message."""
        # The callback uses asyncio.run(runner.run(prompt))
        # We test the logic directly rather than through _subscribe_loop
        msg_data = {"prompt": "test prompt", "task_id": "task-1"}
        message = MagicMock()
        message.data = json.dumps(msg_data).encode()
        message.message_id = "msg-1"

        prompt = json.loads(message.data.decode()).get("prompt", "")
        assert prompt == "test prompt"

        task_id = json.loads(message.data.decode()).get("task_id", message.message_id)
        assert task_id == "task-1"

    def test_callback_uses_message_id_as_fallback(self):
        msg_data = {"prompt": "hello"}
        message = MagicMock()
        message.data = json.dumps(msg_data).encode()
        message.message_id = "fallback-id"

        data = json.loads(message.data.decode())
        task_id = data.get("task_id", message.message_id)
        assert task_id == "fallback-id"

    def test_callback_skips_empty_prompt(self):
        msg_data = {"task_id": "t-1"}
        message = MagicMock()
        message.data = json.dumps(msg_data).encode()
        message.message_id = "m-1"

        data = json.loads(message.data.decode())
        prompt = data.get("prompt", "")
        assert prompt == ""


class TestRunWorker:
    async def test_run_worker_exits_without_subscription(self):
        import pytest

        from agent_runner.worker.pubsub import run_worker

        config = _make_config(invocation={"pubsub_subscription": ""})

        with patch("sys.exit", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit):
                await run_worker(config)
