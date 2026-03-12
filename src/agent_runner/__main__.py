"""Entrypoint: python -m agent_runner."""

from __future__ import annotations

import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(description="Agent Runner")
    parser.add_argument("--config", help="Path to config YAML file")
    parser.add_argument("--task", help="Run a single task and exit (CLI mode)")
    parser.add_argument("--worker", action="store_true", help="Start Pub/Sub worker mode")
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate config and print resolved values, then exit",
    )
    args = parser.parse_args()

    from agent_runner.config import load_config

    try:
        config = load_config(args.config)
    except Exception as exc:
        if args.validate_config:
            print(f"Config validation failed: {exc}", file=sys.stderr)
            sys.exit(1)
        raise

    if args.validate_config:
        _validate_config(config)
        return

    if args.task:
        _run_cli(config, args.task)
    elif args.worker:
        _run_worker(config)
    else:
        _run_server(config)


def _validate_config(config):
    """Print resolved config as YAML and exit."""
    import yaml

    print(yaml.dump(config.model_dump(), default_flow_style=False, sort_keys=False))


def _run_server(config):
    """Start MCP + A2A server via uvicorn."""
    import uvicorn

    from agent_runner.server import create_app

    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port)


def _run_cli(config, task: str):
    """Run a single task via the Claude Agent SDK and exit."""
    from agent_runner.agent import AgentRunner

    runner = AgentRunner(config)
    result = asyncio.run(runner.run(task))
    print(result)


def _run_worker(config):
    """Start Pub/Sub background worker."""
    from agent_runner.worker.pubsub import run_worker

    asyncio.run(run_worker(config))


if __name__ == "__main__":
    main()
