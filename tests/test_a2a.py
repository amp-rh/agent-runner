"""Tests for A2A Agent Card generation."""

from agent_runner.a2a.card import build_agent_card
from agent_runner.config import load_config


def test_build_agent_card_defaults():
    """Agent card builds from default config."""
    config = load_config(path="/nonexistent.yaml")
    card = build_agent_card(config)

    assert card.name == "gcloud-operator"
    assert card.version == "2.0.0"
    assert card.url == "http://localhost:8080"
    assert len(card.skills) == 1
    assert card.skills[0].id == "run_task"


def test_build_agent_card_custom(tmp_path):
    """Agent card reflects custom config."""
    import yaml

    cfg = {
        "agent": {"name": "custom-agent", "description": "My custom agent"},
        "server": {"public_url": "https://example.com"},
        "a2a": {
            "skills": [
                {
                    "id": "analyze",
                    "name": "Analyze data",
                    "description": "Run data analysis",
                    "tags": ["data", "analysis"],
                }
            ]
        },
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg))

    config = load_config(path=cfg_file)
    card = build_agent_card(config)

    assert card.name == "custom-agent"
    assert card.url == "https://example.com"
    assert card.skills[0].id == "analyze"
    assert "data" in card.skills[0].tags
