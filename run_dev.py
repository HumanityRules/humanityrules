"""Development server runner with uvicorn configuration.

Use this for:
- Debugger configurations (VS Code launch.json, PyCharm)
- Running dev server without tailwind: uv run run_dev.py

Note: Don't use from Procfile.tailwind - uvicorn.run() with reload=True
spawns subprocesses that conflict with honcho's process management.
"""

import uvicorn

uvicorn.run(
    app="devopshero_site.asgi:application",
    host="127.0.0.1",
    port=8000,
    reload=True,
    reload_includes=["*.html", "*.css", "*.js"],
    reload_excludes=["tmp/*"],
    log_level="warning",
    timeout_graceful_shutdown=0,
)
