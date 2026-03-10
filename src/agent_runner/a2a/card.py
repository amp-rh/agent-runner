"""Build A2A Agent Card from config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

if TYPE_CHECKING:
    from agent_runner.config import AppConfig


def build_agent_card(config: AppConfig) -> AgentCard:
    """Build an A2A Agent Card from the application config."""
    skills = []
    for skill_cfg in config.a2a.skills:
        skills.append(AgentSkill(
            id=skill_cfg.id,
            name=skill_cfg.name,
            description=skill_cfg.description,
            tags=skill_cfg.tags,
        ))

    return AgentCard(
        name=config.agent.name,
        description=config.agent.description,
        url=config.server.public_url,
        version="2.0.0",
        skills=skills,
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        securitySchemes={
            "bearer": {"type": "http", "scheme": "bearer"},
        },
        security=[{"bearer": []}],
    )
