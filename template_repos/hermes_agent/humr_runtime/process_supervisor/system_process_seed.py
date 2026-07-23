#!/usr/bin/env python3
"""Seed one HUMR-managed process into the system supervisor project."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import process_supervisor


SYSTEM_SLUG_PREFIX = "system."


def _die(message: str) -> None:
    """Terminate with one system-seed error."""
    print(f"process-compose seed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _system_process_entry(slug: str, command: str, cwd: str, environment: list[str]) -> dict:
    """Build one system process-compose entry."""
    return {
        "command": command,
        "working_dir": cwd,
        "log_location": str(process_supervisor.SYSTEM_LOGS_DIR / f"{slug}.log"),
        "log_configuration": process_supervisor.plain_text_log_configuration(),
        "environment": environment,
        "availability": {
            "restart": "on_failure",
            "backoff_seconds": 2,
            "max_restarts": 5,
        },
    }


def main() -> None:
    """Seed one exact system process before its supervisor starts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--command", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE pairs.")
    args = parser.parse_args()

    if not args.slug.startswith(SYSTEM_SLUG_PREFIX):
        _die(
            message=(
                f"system slug must start with {SYSTEM_SLUG_PREFIX!r}: "
                f"got {args.slug!r}"
            )
        )

    working_directory = Path(args.cwd).resolve()
    if not working_directory.is_dir():
        _die(message=f"--cwd {working_directory} is not a directory")

    with process_supervisor.ProcessComposeLock(
        project=process_supervisor.SYSTEM_PROJECT
    ):
        document = process_supervisor.load_process_compose_yaml(
            project=process_supervisor.SYSTEM_PROJECT
        )
        document["processes"][args.slug] = _system_process_entry(
            slug=args.slug,
            command=args.command,
            cwd=str(working_directory),
            environment=list(args.env),
        )
        process_supervisor.save_process_compose_yaml(
            project=process_supervisor.SYSTEM_PROJECT,
            document=document,
        )

    print(f"process-compose seed: {args.slug!r} written.")


if __name__ == "__main__":
    main()
