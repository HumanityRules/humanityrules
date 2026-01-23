"""Development server runner with uvicorn configuration."""

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
