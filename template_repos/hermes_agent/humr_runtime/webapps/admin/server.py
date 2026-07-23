"""Read-only Web Apps status API for the Hermes WebUI sidebar."""

from __future__ import annotations

import sys

sys.path.insert(0, "/opt/humr/runtime/process_supervisor")
sys.path.insert(0, "/opt/humr/runtime/webapps")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

import process_supervisor
import webapps_lib


app = FastAPI(title="HUMR Web Apps Admin API")


def _load_webapp_processes() -> dict[str, dict]:
    """Load only Web App-owned entries from the shared workload document."""
    document = process_supervisor.load_process_compose_yaml(
        project=process_supervisor.APP_WORKLOADS_PROJECT
    )
    return webapps_lib.webapp_processes(document=document)


@app.get("/api/webapps")
def list_webapps() -> dict:
    """List every registered Web App and its observed process state."""
    processes = _load_webapp_processes()
    states = {
        state["name"]: state
        for state in process_supervisor.process_compose_states(
            project=process_supervisor.APP_WORKLOADS_PROJECT
        )
    }
    items = []
    for slug, entry in sorted(processes.items()):
        state = states.get(webapps_lib.webapp_process_name(slug=slug), {})
        routed = webapps_lib.is_routed(entry=entry)
        items.append(
            {
                "slug": slug,
                "port": webapps_lib.port_from_entry(entry=entry),
                "status": state.get("status", "Pending"),
                "is_ready": state.get("is_ready", "Unknown"),
                "restarts": state.get("restarts", 0),
                "routed": routed,
                "url": webapps_lib.url_for(slug=slug) if routed else None,
                "is_internal": webapps_lib.is_internal_slug(slug=slug),
            }
        )
    return {"items": items}


@app.get("/api/webapps/{slug}")
def detail(slug: str) -> dict:
    """Return details for one Web App-owned workload."""
    entry = _load_webapp_processes().get(slug)
    if entry is None:
        raise HTTPException(status_code=404)
    state = process_supervisor.process_compose_state_for(
        project=process_supervisor.APP_WORKLOADS_PROJECT,
        process_name=webapps_lib.webapp_process_name(slug=slug),
    ) or {}
    routed = webapps_lib.is_routed(entry=entry)
    return {
        "slug": slug,
        "command": entry.get("command"),
        "working_dir": entry.get("working_dir"),
        "environment": entry.get("environment", []),
        "port": webapps_lib.port_from_entry(entry=entry),
        "status": state.get("status"),
        "is_ready": state.get("is_ready"),
        "restarts": state.get("restarts", 0),
        "routed": routed,
        "url": webapps_lib.url_for(slug=slug) if routed else None,
    }


@app.get("/api/webapps/{slug}/logs")
def logs(slug: str, request: Request) -> Response:
    """Return the tail of one registered Web App's log."""
    if slug not in _load_webapp_processes():
        raise HTTPException(status_code=404)
    format_name = request.query_params.get("format")
    tail_value = request.query_params.get("tail", "200")
    try:
        tail = int(tail_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="tail must be an integer") from exc
    log_path = webapps_lib.WEBAPP_LOGS_DIR / f"{slug}.log"
    if not log_path.exists():
        if format_name == "text":
            return PlainTextResponse("", media_type="text/plain; charset=utf-8")
        return JSONResponse(content={"lines": []})
    bounded_tail = max(1, min(tail, 2000))
    with log_path.open(encoding="utf-8") as handle:
        lines = handle.readlines()[-bounded_tail:]
    if format_name == "text":
        return PlainTextResponse(
            "".join(lines),
            media_type="text/plain; charset=utf-8",
        )
    return JSONResponse(content={"lines": lines})
