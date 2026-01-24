"""Development server runner with uvicorn configuration.

Use this for:
- Debugger configurations (VS Code launch.json, PyCharm)
- Running dev server without tailwind: uv run run_dev.py

Note: Don't use from Procfile.tailwind - uvicorn.run() with reload=True
spawns subprocesses that conflict with honcho's process management.
"""

from pathlib import Path

import uvicorn

# Must use absolute path - uvicorn's FileFilter compares Path objects directly
# and watchfiles reports absolute paths, so relative paths never match
TMP_DIR = str(Path("tmp").resolve())

uvicorn.run(
    app="devopshero_site.asgi:application",
    host="127.0.0.1",
    port=8000,
    reload=True,
    reload_includes=["*.html", "*.css", "*.js"],
    reload_excludes=[TMP_DIR],
    log_level="warning",
    timeout_graceful_shutdown=0,
)
