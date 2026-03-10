"""YAML config loader with env var overrides and pydantic validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    name: str = "gcloud-operator"
    description: str = "Claude agent"
    model: str = "claude-sonnet-4-6"
    system_prompt: str = ""
    max_turns: int = 50
    timeout: int = 600
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    ])


class McpServerStdioConfig(BaseModel):
    type: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpServerUrlConfig(BaseModel):
    type: str = "url"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class SubagentConfig(BaseModel):
    type: str = "local"  # "local" or "remote"
    description: str = ""
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    url: str | None = None
    discovery: str | None = None  # "registry" for Firestore lookup


class A2ASkillConfig(BaseModel):
    id: str = "run_task"
    name: str = "Execute agent task"
    description: str = "Run a task using this agent"
    tags: list[str] = Field(default_factory=lambda: ["general"])


class A2AConfig(BaseModel):
    enabled: bool = True
    skills: list[A2ASkillConfig] = Field(default_factory=lambda: [A2ASkillConfig()])


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    public_url: str = "http://localhost:8080"
    min_instances: int = 0


class InvocationConfig(BaseModel):
    http: bool = True
    streaming: bool = True
    pubsub_enabled: bool = False
    pubsub_subscription: str = ""


class ReflectionHookConfig(BaseModel):
    enabled: bool = True
    firestore_project: str = "claude-connectors"
    firestore_database: str = "agents"
    collection: str = "session_learnings"


class AuditHookConfig(BaseModel):
    enabled: bool = True


class HooksConfig(BaseModel):
    reflection: ReflectionHookConfig = Field(default_factory=ReflectionHookConfig)
    audit: AuditHookConfig = Field(default_factory=AuditHookConfig)


class GCPConfig(BaseModel):
    project: str = "claude-connectors"
    region: str = "us-central1"


class AppConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    subagents: dict[str, SubagentConfig] = Field(default_factory=dict)
    a2a: A2AConfig = Field(default_factory=A2AConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    invocation: InvocationConfig = Field(default_factory=InvocationConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    gcp: GCPConfig = Field(default_factory=GCPConfig)


CONFIG_PATHS = [
    Path("/etc/agent-runner/config.yaml"),
    Path("agent-config.yaml"),
]

ENV_PREFIX = "AGENT_CONFIG_"


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides.

    Variables like AGENT_CONFIG_AGENT__NAME=x set data["agent"]["name"] = "x".
    Double underscore separates nesting levels.
    """
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        parts = key[len(ENV_PREFIX):].lower().split("__")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return data


def _apply_legacy_env(data: dict[str, Any]) -> dict[str, Any]:
    """Map legacy env vars to config fields for backward compatibility."""
    mappings = {
        "AGENT_NAME": ("agent", "name"),
        "AGENT_TIMEOUT": ("agent", "timeout"),
        "GCP_PROJECT": ("gcp", "project"),
        "PUBLIC_URL": ("server", "public_url"),
        "PORT": ("server", "port"),
    }
    for env_var, path in mappings.items():
        val = os.environ.get(env_var)
        if val is not None:
            target = data
            for part in path[:-1]:
                target = target.setdefault(part, {})
            # Don't override if already set by AGENT_CONFIG_ prefix
            if path[-1] not in target:
                target[path[-1]] = int(val) if val.isdigit() else val
    return data


def _apply_firestore_config(data: dict[str, Any]) -> dict[str, Any]:
    """Merge config fields from Firestore agents/{name} document.

    Precedence: YAML < Firestore < env vars. Firestore values override
    YAML defaults but are themselves overridden by env var settings.
    Failure is non-fatal (logged to stderr).
    """
    agent_name = data.get("agent", {}).get("name")
    if not agent_name:
        return data

    try:
        from google.cloud.firestore import Client

        project = data.get("gcp", {}).get("project", "claude-connectors")
        db = Client(project=project, database="agents")
        doc = db.collection("agents").document(agent_name).get()
        if doc.exists:
            fs_data = doc.to_dict()
            agent = data.setdefault("agent", {})
            for field in ("system_prompt", "description", "model", "timeout", "max_turns"):
                if field in fs_data:
                    agent[field] = fs_data[field]
    except Exception as exc:
        import sys

        print(f"Firestore config lookup failed (non-fatal): {exc}", file=sys.stderr)

    return data


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load config from YAML file with env var overrides."""
    data: dict[str, Any] = {}

    if path:
        config_path = Path(path)
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text()) or {}
    else:
        for config_path in CONFIG_PATHS:
            if config_path.exists():
                data = yaml.safe_load(config_path.read_text()) or {}
                break

    _apply_firestore_config(data)
    _apply_legacy_env(data)
    _apply_env_overrides(data)

    return AppConfig(**data)
