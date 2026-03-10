"""Tests for config loading, env overrides, and validation."""

import yaml

from agent_runner.config import load_config


def test_default_config():
    """Loading with no file returns valid defaults."""
    config = load_config(path="/nonexistent/path.yaml")
    assert config.agent.name == "gcloud-operator"
    assert config.agent.model == "claude-sonnet-4-6"
    assert config.agent.timeout == 600
    assert config.server.port == 8080
    assert config.gcp.project == "claude-connectors"


def test_yaml_loading(tmp_path):
    """Config loads from a YAML file."""
    cfg = {
        "agent": {"name": "test-agent", "model": "claude-opus-4-6", "timeout": 600},
        "server": {"port": 9090},
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    config = load_config(path=cfg_file)
    assert config.agent.name == "test-agent"
    assert config.agent.model == "claude-opus-4-6"
    assert config.agent.timeout == 600
    assert config.server.port == 9090
    # Defaults preserved for unset fields
    assert config.gcp.project == "claude-connectors"


def test_env_override(monkeypatch, tmp_path):
    """AGENT_CONFIG_* env vars override YAML values."""
    cfg = {"agent": {"name": "yaml-agent"}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    monkeypatch.setenv("AGENT_CONFIG_AGENT__NAME", "env-agent")
    monkeypatch.setenv("AGENT_CONFIG_SERVER__PORT", "3000")

    config = load_config(path=cfg_file)
    assert config.agent.name == "env-agent"
    assert config.server.port == 3000


def test_legacy_env_vars(monkeypatch):
    """Legacy env vars (AGENT_NAME, PORT, etc.) are mapped to config."""
    monkeypatch.setenv("AGENT_NAME", "legacy-agent")
    monkeypatch.setenv("PORT", "4000")
    monkeypatch.setenv("GCP_PROJECT", "my-project")

    config = load_config(path="/nonexistent.yaml")
    assert config.agent.name == "legacy-agent"
    assert config.server.port == 4000
    assert config.gcp.project == "my-project"


def test_subagent_config(tmp_path):
    """Subagent config parses correctly."""
    cfg = {
        "subagents": {
            "reviewer": {
                "type": "local",
                "description": "Code reviewer",
                "prompt": "Review this code",
                "tools": ["Read", "Grep"],
                "model": "claude-haiku-4-5",
            },
            "remote-agent": {
                "type": "remote",
                "url": "https://example.com",
            },
        }
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    config = load_config(path=cfg_file)
    assert "reviewer" in config.subagents
    assert config.subagents["reviewer"].type == "local"
    assert config.subagents["reviewer"].model == "claude-haiku-4-5"
    assert config.subagents["remote-agent"].type == "remote"


def test_a2a_config_defaults():
    """A2A config has sensible defaults."""
    config = load_config(path="/nonexistent.yaml")
    assert config.a2a.enabled is True
    assert len(config.a2a.skills) == 1
    assert config.a2a.skills[0].id == "run_task"


def test_hooks_config_defaults():
    """Hooks config has reflection and audit enabled by default."""
    config = load_config(path="/nonexistent.yaml")
    assert config.hooks.reflection.enabled is True
    assert config.hooks.audit.enabled is True
    assert config.hooks.reflection.collection == "session_learnings"


def test_empty_yaml(tmp_path):
    """Empty YAML file returns defaults."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("")

    config = load_config(path=cfg_file)
    assert config.agent.name == "gcloud-operator"


def test_firestore_config_merged(monkeypatch, tmp_path):
    """Firestore config fields are merged after YAML, before env vars."""
    from unittest.mock import MagicMock, patch

    cfg = {"agent": {"name": "test-agent"}, "gcp": {"project": "test-proj"}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    # Mock Firestore client
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "system_prompt": "You are a test agent.",
        "description": "Test agent from Firestore",
        "timeout": 900,
    }

    mock_collection = MagicMock()
    mock_collection.document.return_value.get.return_value = mock_doc

    mock_client = MagicMock()
    mock_client.return_value.collection.return_value = mock_collection

    with patch("agent_runner.config.Client", mock_client, create=True):
        # Patch the import inside _apply_firestore_config
        import agent_runner.config as config_mod
        original = config_mod._apply_firestore_config

        def patched_apply(data):
            import sys
            mock_module = MagicMock()
            mock_module.Client = mock_client
            sys.modules["google.cloud.firestore"] = mock_module
            try:
                return original(data)
            finally:
                del sys.modules["google.cloud.firestore"]

        with patch.object(config_mod, "_apply_firestore_config", patched_apply):
            config = load_config(path=cfg_file)

    assert config.agent.system_prompt == "You are a test agent."
    assert config.agent.description == "Test agent from Firestore"
    assert config.agent.timeout == 900


def test_firestore_config_env_override_takes_precedence(monkeypatch, tmp_path):
    """Env var overrides take precedence over Firestore values."""
    from unittest.mock import MagicMock, patch

    cfg = {"agent": {"name": "test-agent"}, "gcp": {"project": "test-proj"}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"description": "From Firestore"}

    mock_collection = MagicMock()
    mock_collection.document.return_value.get.return_value = mock_doc
    mock_client = MagicMock()
    mock_client.return_value.collection.return_value = mock_collection

    monkeypatch.setenv("AGENT_CONFIG_AGENT__DESCRIPTION", "From env")

    import agent_runner.config as config_mod
    original = config_mod._apply_firestore_config

    def patched_apply(data):
        mock_module = MagicMock()
        mock_module.Client = mock_client
        import sys
        sys.modules["google.cloud.firestore"] = mock_module
        try:
            return original(data)
        finally:
            del sys.modules["google.cloud.firestore"]

    with patch.object(config_mod, "_apply_firestore_config", patched_apply):
        config = load_config(path=cfg_file)

    # Env var should win over Firestore
    assert config.agent.description == "From env"


def test_firestore_config_failure_nonfatal(tmp_path, capsys):
    """Firestore lookup failure is non-fatal."""
    from unittest.mock import MagicMock, patch

    cfg = {"agent": {"name": "test-agent"}, "gcp": {"project": "test-proj"}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    import agent_runner.config as config_mod
    original = config_mod._apply_firestore_config

    def patched_apply(data):
        mock_module = MagicMock()
        mock_module.Client.side_effect = ConnectionError("Firestore unavailable")
        import sys
        sys.modules["google.cloud.firestore"] = mock_module
        try:
            return original(data)
        finally:
            del sys.modules["google.cloud.firestore"]

    with patch.object(config_mod, "_apply_firestore_config", patched_apply):
        config = load_config(path=cfg_file)

    # Should succeed with defaults despite Firestore failure
    assert config.agent.name == "test-agent"
    captured = capsys.readouterr()
    assert "non-fatal" in captured.err
