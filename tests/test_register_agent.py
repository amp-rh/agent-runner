"""Tests for scripts/register_agent.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from register_agent import _parse_markdown, _parse_yaml_config, load_agent_file, validate_agent_data, register


class TestParseMarkdown:
    def test_parses_frontmatter_and_body(self):
        text = "---\nname: my-agent\ndescription: Test\n---\n\nSystem prompt here."
        result = _parse_markdown(text)
        assert result["name"] == "my-agent"
        assert result["description"] == "Test"
        assert result["system_prompt"] == "System prompt here."

    def test_frontmatter_system_prompt_not_overridden_by_body(self):
        text = "---\nname: my-agent\nsystem_prompt: from frontmatter\n---\n\nBody text."
        result = _parse_markdown(text)
        assert result["system_prompt"] == "from frontmatter"

    def test_no_frontmatter_returns_body_as_system_prompt(self):
        text = "Just some text without frontmatter."
        result = _parse_markdown(text)
        assert result["system_prompt"] == text

    def test_empty_body_not_added_as_system_prompt(self):
        text = "---\nname: my-agent\n---\n\n"
        result = _parse_markdown(text)
        assert "system_prompt" not in result or result.get("system_prompt") == ""


class TestParseYamlConfig:
    def test_parses_agent_section(self):
        text = "agent:\n  name: test-agent\n  description: A test\n  model: claude-sonnet-4-6\n"
        result = _parse_yaml_config(text)
        assert result["name"] == "test-agent"
        assert result["description"] == "A test"
        assert result["model"] == "claude-sonnet-4-6"

    def test_empty_yaml_returns_empty_dict(self):
        result = _parse_yaml_config("")
        assert result == {}


class TestLoadAgentFile:
    def test_loads_markdown_file(self, tmp_path):
        md = tmp_path / "agent.md"
        md.write_text("---\nname: md-agent\ndescription: Markdown agent\n---\n\nPrompt text.")
        result = load_agent_file(md)
        assert result["name"] == "md-agent"
        assert result["system_prompt"] == "Prompt text."

    def test_loads_yaml_file(self, tmp_path):
        yml = tmp_path / "agent.yaml"
        yml.write_text("agent:\n  name: yaml-agent\n  description: YAML agent\n")
        result = load_agent_file(yml)
        assert result["name"] == "yaml-agent"


class TestValidateAgentData:
    def test_valid_data_returns_filtered_fields(self, tmp_path):
        data = {
            "name": "test-agent",
            "description": "desc",
            "model": "claude-sonnet-4-6",
            "system_prompt": "prompt",
            "timeout": 300,
            "max_turns": 50,
            "color": "cyan",  # not in allowed fields
        }
        result = validate_agent_data(data, tmp_path / "agent.md")
        assert set(result.keys()) == {"name", "description", "model", "system_prompt", "timeout", "max_turns"}
        assert "color" not in result

    def test_missing_name_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            validate_agent_data({"description": "no name"}, tmp_path / "agent.md")


class TestRegister:
    def test_register_writes_to_firestore(self, tmp_path):
        md = tmp_path / "agent.md"
        md.write_text("---\nname: test-agent\ndescription: Test\n---\n\nSystem prompt.")

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        with patch("google.cloud.firestore.Client", return_value=mock_db):
            register(str(md), "test-project")

        mock_db.collection.assert_called_once_with("agents")
        mock_db.collection.return_value.document.assert_called_once_with("test-agent")
        mock_doc_ref.set.assert_called_once()
        call_args = mock_doc_ref.set.call_args
        doc_data = call_args[0][0]
        assert doc_data["name"] == "test-agent"
        assert doc_data["system_prompt"] == "System prompt."
        assert call_args[1]["merge"] is True

    def test_register_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            register(str(tmp_path / "nonexistent.md"), "test-project")

    def test_register_firestore_error_exits(self, tmp_path):
        md = tmp_path / "agent.md"
        md.write_text("---\nname: test-agent\n---\n")

        with patch("google.cloud.firestore.Client", side_effect=Exception("connection error")):
            with pytest.raises(SystemExit):
                register(str(md), "test-project")
