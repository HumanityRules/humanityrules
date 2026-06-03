#!/usr/bin/env python3
"""Exec a child process after overlaying the active Hermes profile `.env`."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_line(raw: str) -> tuple[str, str] | None:
    """Parse one dotenv line without executing shell syntax."""
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line.removeprefix("export ").lstrip()
    key, value = line.split("=", 1)
    key = key.strip()
    if not ENV_KEY_PATTERN.match(key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Read key/value pairs from a profile dotenv file."""
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    except OSError as exc:
        print(f"profile_env_exec: could not read {env_path}: {exc}", file=sys.stderr)
        return values
    for raw in lines:
        parsed = _parse_env_line(raw=raw)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def main(argv: list[str]) -> None:
    """Overlay profile env and replace this process with the target command."""
    if len(argv) < 2:
        print("usage: profile_env_exec.py COMMAND [ARG ...]", file=sys.stderr)
        raise SystemExit(2)
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        os.environ.update(_load_env_file(env_path=Path(hermes_home) / ".env"))
    os.execvp(argv[1], argv[1:])


if __name__ == "__main__":
    main(argv=sys.argv)
