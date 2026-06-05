"""DOH `__admin` webapp: read-only JSON API for the WebUI sidebar.

Served at /webapps/__admin/ via the same Caddy → process-compose path as user
webapps. v1 surface is webapps list/detail/logs; the slug name (no "webapps")
leaves room for general runtime admin endpoints later.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/doh/runtime")

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from webapps_lib import (
    LOGS_DIR,
    SYSTEM_SLUG_PREFIX,
    WEBAPPS_PROJECT,
    is_routed,
    load_process_compose_yaml,
    port_from_entry,
    url_for,
    process_compose_states,
    process_compose_state_for,
)

app = FastAPI(title="DOH Admin API")


@app.get("/api/webapps")
def list_webapps() -> dict:
    doc = load_process_compose_yaml(project=WEBAPPS_PROJECT)
    states = {s["name"]: s for s in process_compose_states(project=WEBAPPS_PROJECT)}
    items = []
    for slug, entry in sorted(doc.get("processes", {}).items()):
        st = states.get(slug, {})
        items.append({
            "slug": slug,
            "port": port_from_entry(entry),
            "status": st.get("status", "Pending"),
            "is_ready": st.get("is_ready", "Unknown"),
            "restarts": st.get("restarts", 0),
            "routed": is_routed(entry),
            "url": url_for(slug) if is_routed(entry) else None,
            "is_internal": slug.startswith("__") or slug.startswith(SYSTEM_SLUG_PREFIX),
        })
    return {"items": items}


@app.get("/api/webapps/{slug}")
def detail(slug: str) -> dict:
    doc = load_process_compose_yaml(project=WEBAPPS_PROJECT)
    entry = doc.get("processes", {}).get(slug)
    if entry is None:
        raise HTTPException(status_code=404)
    st = process_compose_state_for(project=WEBAPPS_PROJECT, slug=slug) or {}
    return {
        "slug": slug,
        "command": entry.get("command"),
        "working_dir": entry.get("working_dir"),
        "environment": entry.get("environment", []),
        "port": port_from_entry(entry),
        "status": st.get("status"),
        "is_ready": st.get("is_ready"),
        "restarts": st.get("restarts", 0),
        "routed": is_routed(entry),
        "url": url_for(slug) if is_routed(entry) else None,
    }


@app.get("/api/webapps/{slug}/logs")
def logs(slug: str, tail: int = 200, format: str | None = None):
    log_path = LOGS_DIR / f"{slug}.log"
    if not log_path.exists():
        if format == "text":
            return PlainTextResponse("", media_type="text/plain; charset=utf-8")
        return {"lines": []}
    tail = max(1, min(tail, 2000))
    with log_path.open() as f:
        lines = f.readlines()[-tail:]
    if format == "text":
        return PlainTextResponse("".join(lines), media_type="text/plain; charset=utf-8")
    return {"lines": lines}
