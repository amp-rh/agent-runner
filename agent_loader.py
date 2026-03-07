"""Load and register agent configurations via Firestore.

Startup mode (default): fetches agent config from Firestore and writes
the .md file locally for Claude CLI to use.

Register mode (--register): parses a local .md file and pushes it to Firestore.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

AGENT_DIR = os.path.join(os.environ.get("HOME", "/home/user"), ".claude", "agents")
DEFAULT_DATABASE = "agents"
DEFAULT_COLLECTION = "agents"


def _firestore_client(project: str, database: str = DEFAULT_DATABASE):
    from google.cloud.firestore import Client

    return Client(project=project, database=database)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (metadata, body)."""
    if not content.startswith("---"):
        return {}, content

    end = content.find("---", 3)
    if end == -1:
        return {}, content

    frontmatter_text = content[3:end].strip()
    body = content[end + 3:].strip()

    metadata = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        metadata[key] = value

    return metadata, body


def _build_frontmatter(data: dict) -> str:
    lines = []
    for key in ("name", "description", "model", "color", "memory"):
        if key in data:
            value = data[key]
            if isinstance(value, str) and ("\n" in value or '"' in value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def load_agent(project: str) -> str:
    """Fetch agent config from Firestore and write the .md file locally."""
    agent_id = os.environ.get("AGENT_ID") or os.environ.get("AGENT_NAME")
    if not agent_id:
        return os.environ.get("AGENT_NAME", "gcloud-operator")

    try:
        db = _firestore_client(project)
        doc = db.collection(DEFAULT_COLLECTION).document(agent_id).get()

        if not doc.exists:
            print(f"Agent '{agent_id}' not found in Firestore, using fallback", file=sys.stderr)
            return agent_id

        data = doc.to_dict()
        if not data.get("enabled", True):
            print(f"Agent '{agent_id}' is disabled", file=sys.stderr)
            sys.exit(1)

        name = data.get("name", agent_id)
        frontmatter = _build_frontmatter(data)
        body = data.get("system_prompt", "")
        content = f"---\n{frontmatter}---\n\n{body}\n"

        os.makedirs(AGENT_DIR, exist_ok=True)
        path = os.path.join(AGENT_DIR, f"{name}.md")
        with open(path, "w") as f:
            f.write(content)

        print(f"Loaded agent '{name}' from Firestore", file=sys.stderr)
        return name

    except ImportError:
        print("google-cloud-firestore not installed, using fallback agent", file=sys.stderr)
        return agent_id
    except Exception as exc:
        print(f"Firestore load failed: {exc}, using fallback agent", file=sys.stderr)
        return agent_id


def register_agent(filepath: str, project: str):
    """Parse a local .md agent file and push it to Firestore."""
    with open(filepath) as f:
        content = f.read()

    metadata, body = _parse_frontmatter(content)
    name = metadata.get("name")
    if not name:
        print("Error: agent .md file must have 'name' in frontmatter", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    doc_data = {
        "name": name,
        "system_prompt": body,
        "enabled": True,
        "updated_at": now,
    }

    for key in ("description", "model", "color", "memory"):
        if key in metadata:
            doc_data[key] = metadata[key]

    if "timeout" in metadata:
        doc_data["timeout"] = int(metadata["timeout"])

    db = _firestore_client(project)
    doc_ref = db.collection(DEFAULT_COLLECTION).document(name)

    existing = doc_ref.get()
    if not existing.exists:
        doc_data["created_at"] = now

    doc_ref.set(doc_data, merge=True)
    print(f"Registered agent '{name}' in Firestore (project={project}, database={DEFAULT_DATABASE})")


def main():
    parser = argparse.ArgumentParser(description="Agent loader for Firestore")
    parser.add_argument("--register", metavar="FILE", help="Register a local .md agent file to Firestore")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT", "claude-connectors"))
    args = parser.parse_args()

    if args.register:
        register_agent(args.register, args.project)
    else:
        name = load_agent(args.project)
        print(name)


if __name__ == "__main__":
    main()
