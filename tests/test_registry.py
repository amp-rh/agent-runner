"""Tests for Firestore registry and Pub/Sub capability announcements."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from agent_runner.registry.firestore import advertise, discover, list_peers
from agent_runner.registry.pubsub import publish_capability


class TestAdvertise:
    def test_advertise_upserts_doc(self):
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_doc

        with patch("agent_runner.registry.firestore._firestore_client", return_value=mock_db):
            advertise(
                agent_name="test-agent",
                service_url="https://test.example.com",
                capabilities=["search", "analyze"],
                description="Test agent",
                project="test-project",
            )

        mock_collection.document.assert_called_once_with("test-agent")
        set_args = mock_doc.set.call_args
        doc_data = set_args[0][0]
        assert doc_data["name"] == "test-agent"
        assert doc_data["service_url"] == "https://test.example.com"
        assert doc_data["capabilities"] == ["search", "analyze"]
        assert doc_data["status"] == "online"
        assert "last_heartbeat" in doc_data
        # merge=True
        assert set_args[1]["merge"] is True

    def test_advertise_handles_failure(self, capsys):
        with patch(
            "agent_runner.registry.firestore._firestore_client",
            side_effect=Exception("connection error"),
        ):
            # Should not raise
            advertise("agent", "url", [], "desc", "proj")

        captured = capsys.readouterr()
        assert "Failed to update registry" in captured.err


class TestDiscover:
    def test_discover_returns_online_agents(self):
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {
            "name": "agent-1",
            "status": "online",
            "capabilities": ["search"],
        }
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {
            "name": "agent-2",
            "status": "online",
            "capabilities": ["deploy"],
        }

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc1, mock_doc2]
        mock_db.collection.return_value.where.return_value = mock_query

        with patch("agent_runner.registry.firestore._firestore_client", return_value=mock_db):
            results = discover(project="test-project")

        assert len(results) == 2
        assert results[0]["name"] == "agent-1"

    def test_discover_filters_by_capability(self):
        mock_doc1 = MagicMock()
        mock_doc1.to_dict.return_value = {
            "name": "agent-1",
            "capabilities": ["search"],
        }
        mock_doc2 = MagicMock()
        mock_doc2.to_dict.return_value = {
            "name": "agent-2",
            "capabilities": ["deploy"],
        }

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc1, mock_doc2]
        mock_db.collection.return_value.where.return_value = mock_query

        with patch("agent_runner.registry.firestore._firestore_client", return_value=mock_db):
            results = discover(capability="search", project="test-project")

        assert len(results) == 1
        assert results[0]["name"] == "agent-1"

    def test_list_peers_delegates_to_discover(self):
        with patch("agent_runner.registry.firestore.discover", return_value=[]) as mock:
            result = list_peers(project="test-project")

        mock.assert_called_once_with(project="test-project")
        assert result == []


class TestPublishCapability:
    def test_publish_sends_message(self):
        mock_publisher = MagicMock()

        with patch(
            "google.cloud.pubsub_v1.PublisherClient",
            return_value=mock_publisher,
        ):
            publish_capability(
                project="test-project",
                agent_name="test-agent",
                service_url="https://test.example.com",
                capabilities=["search"],
                description="Test agent",
            )

        mock_publisher.publish.assert_called_once()
        topic = mock_publisher.publish.call_args[0][0]
        assert topic == "projects/test-project/topics/agent-capabilities"

        msg_bytes = mock_publisher.publish.call_args[0][1]
        msg = json.loads(msg_bytes.decode())
        assert msg["event"] == "agent_online"
        assert msg["agent"] == "test-agent"
        assert msg["capabilities"] == ["search"]

    def test_publish_handles_failure(self, capsys):
        with patch(
            "google.cloud.pubsub_v1.PublisherClient",
            side_effect=Exception("pubsub error"),
        ):
            # Should not raise
            publish_capability("proj", "agent", "url", [], "desc")

        captured = capsys.readouterr()
        assert "Failed to publish" in captured.err
