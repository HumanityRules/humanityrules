"""Development server runner with uvicorn configuration.

Use this for:
- Debugger configurations (VS Code launch.json, PyCharm)
- Running dev server without tailwind: uv run run_dev.py

Note: Uses subprocess to call uvicorn CLI because uvicorn.run() with reload=True
has socket binding issues on macOS. The CLI handles this correctly.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path


def kill_process_on_port(port):
    """Kill any process listening on the specified port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            print(f"Killing existing process(es) on port {port}: {', '.join(pids)}")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
    except FileNotFoundError:
        pass  # lsof not available


kill_process_on_port(8000)

# Get absolute path to sandbox directory for exclusion
TMP_DIR = str(Path("sandbox").resolve())

# Build uvicorn command matching Procfile.tailwind
cmd = [
    sys.executable,
    "-m",
    "uvicorn",
    "devopshero_site.asgi:application",
    "--reload",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
    "--timeout-graceful-shutdown",
    "0",
    "--reload-exclude",
    TMP_DIR,
    "--reload-include",
    "*.html",
    "--reload-include",
    "*.css",
    "--reload-include",
    "*.js",
    "--log-level",
    "warning",
]

# Run uvicorn, replacing this process
os.execvp(sys.executable, cmd)
