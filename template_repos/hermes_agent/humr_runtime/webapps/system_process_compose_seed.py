#!/usr/bin/env python3
"""Seed a `system.<name>` entry into the system process-compose YAML.

System processes (e.g. `system.gateway`) are DOH-managed and live in a
separate process-compose project from webapps. This keeps webapp CRUD reloads
from touching the WebUI or gateway supervisor.

Usage:
    system_process_compose_seed.py system.gateway --command "..." --cwd /path [--env K=V ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from webapps_lib import (
    LOGS_DIR,
    SYSTEM_PROJECT,
    SYSTEM_SLUG_PREFIX,
    ProcessComposeLock,
    die,
    load_process_compose_yaml,
    plain_text_log_configuration,
    save_process_compose_yaml,
)


def _system_process_entry(slug: str, command: str, cwd: str, env_pairs: list[str]) -> dict:
    return {
        "command": command,
        "working_dir": cwd,
        "log_location": str(LOGS_DIR / f"{slug}.log"),
        "log_configuration": plain_text_log_configuration(),
        "environment": env_pairs,
        "availability": {
            "restart": "on_failure",
            "backoff_seconds": 2,
            "max_restarts": 5,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--command", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE pairs.")
    args = parser.parse_args()

    if not args.slug.startswith(SYSTEM_SLUG_PREFIX):
        die(f"system slug must start with {SYSTEM_SLUG_PREFIX!r}: got {args.slug!r}")

    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        die(f"--cwd {cwd} is not a directory")

    with ProcessComposeLock(project=SYSTEM_PROJECT):
        doc = load_process_compose_yaml(project=SYSTEM_PROJECT)
        # Always rewrite — keeps the entry in sync with the latest command/env.
        doc["processes"][args.slug] = _system_process_entry(
            slug=args.slug,
            command=args.command,
            cwd=str(cwd),
            env_pairs=list(args.env),
        )
        save_process_compose_yaml(project=SYSTEM_PROJECT, doc=doc)

    print(f"process-compose seed: {args.slug!r} written.")


if __name__ == "__main__":
    main()
