"""Register an agent configuration file into Firestore agents/{name} document."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


ALLOWED_FIELDS = ("name", "description", "model", "system_prompt", "timeout", "max_turns")


def _parse_markdown(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter + body from a markdown agent file."""
    if not text.startswith("---"):
        return {"system_prompt": text.strip()}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"system_prompt": text.strip()}

    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    if body:
        frontmatter.setdefault("system_prompt", body)
    return frontmatter


def _parse_yaml_config(text: str) -> dict[str, Any]:
    """Parse a full agent-config.yaml and flatten agent section to top-level fields."""
    data = yaml.safe_load(text) or {}
    agent_section = data.get("agent", {})
    # Accept both flat and nested formats
    return {**agent_section, **{k: v for k, v in data.items() if k != "agent"}}


def load_agent_file(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix == ".md":
        return _parse_markdown(text)
    return _parse_yaml_config(text)


def validate_agent_data(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Validate required fields and filter to allowed Firestore fields."""
    name = data.get("name")
    if not name:
        print(f"Error: 'name' field is required in {path}", file=sys.stderr)
        sys.exit(1)

    filtered = {k: v for k, v in data.items() if k in ALLOWED_FIELDS and v is not None}
    if "name" not in filtered:
        print(f"Error: 'name' field is required in {path}", file=sys.stderr)
        sys.exit(1)

    return filtered


def register(agent_file: str, project: str) -> None:
    path = Path(agent_file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    raw = load_agent_file(path)
    agent_data = validate_agent_data(raw, path)
    name = agent_data["name"]

    try:
        from google.cloud.firestore import Client

        db = Client(project=project, database="agents")
        doc_ref = db.collection("agents").document(name)
        doc_ref.set(agent_data, merge=True)
        print(f"Agent '{name}' registered in Firestore (project={project})")
    except Exception as exc:
        print(f"Error: failed to write to Firestore: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Register an agent config into Firestore.")
    parser.add_argument("agent_file", help="Path to agent .md or .yaml config file")
    parser.add_argument("--project", default="claude-connectors", help="GCP project ID")
    args = parser.parse_args()

    register(args.agent_file, args.project)
