"""Web App ownership, routing, and process-entry helpers."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "process_supervisor"))

import process_supervisor


WEBAPP_PROJECTS_DIR = Path("/workspace/webapps/projects")
WEBAPP_LOGS_DIR = Path("/workspace/webapps/logs")
WEBAPP_ROUTES_PATH = Path("/workspace/.config/caddy/webapps.caddy")

SLUG_PATTERN = re.compile(r"^(?:__)?[a-z][a-z0-9-]{0,30}[a-z0-9]$")
INTERNAL_SLUG_PREFIX = "__"
SYSTEM_SLUG_PREFIX = "system."
PUBLIC_HOSTNAME_ENV = "HUMR_PUBLIC_HOSTNAME"


def die(message: str, code: int) -> None:
    """Print one Web Apps CLI error and terminate."""
    print(f"webapps: {message}", file=sys.stderr)
    sys.exit(code)


def ensure_webapp_layout() -> None:
    """Create Web App-owned source, log, and route locations when absent."""
    WEBAPP_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    WEBAPP_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    WEBAPP_ROUTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not WEBAPP_ROUTES_PATH.exists():
        WEBAPP_ROUTES_PATH.write_text("# no Web App routes\n", encoding="utf-8")


def validate_slug(slug: str) -> None:
    """Validate a Web App product slug."""
    if slug.startswith(SYSTEM_SLUG_PREFIX):
        die(
            message=(
                f"invalid slug {slug!r}: the {SYSTEM_SLUG_PREFIX!r} prefix is "
                "reserved for HUMR-managed system processes; pick another name"
            ),
            code=1,
        )
    if SLUG_PATTERN.fullmatch(slug) is None:
        die(
            message=(
                f"invalid slug {slug!r}: lowercase letters/digits/hyphens, "
                "2-32 chars, must start with letter and end alphanumeric "
                "(optional `__` prefix reserved for platform internals)"
            ),
            code=1,
        )


def is_internal_slug(slug: str) -> bool:
    """Return whether a Web App slug is platform-internal."""
    return slug.startswith(INTERNAL_SLUG_PREFIX)


def webapp_process_name(slug: str) -> str:
    """Map a Web App slug into the shared supervisor namespace."""
    return process_supervisor.workload_process_name(
        kind=process_supervisor.WEBAPP_WORKLOAD_KIND,
        slug=slug,
    )


def webapp_processes(document: dict) -> dict[str, dict]:
    """Return Web App workload entries keyed by product slug."""
    return process_supervisor.workloads_for_kind(
        document=document,
        kind=process_supervisor.WEBAPP_WORKLOAD_KIND,
    )


def webapp_process_entry(document: dict, slug: str) -> dict | None:
    """Return one Web App-owned entry without exposing another workload kind."""
    entry = document.get("processes", {}).get(webapp_process_name(slug=slug))
    return entry if isinstance(entry, dict) else None


def matcher_name(slug: str) -> str:
    """Build a safe Caddy named-matcher token for one Web App."""
    return "webapp_" + slug.replace("-", "_").lstrip("_")


def public_hostname() -> str:
    """Read the agent's public hostname from the env-bearer overlay."""
    host = os.environ.get(PUBLIC_HOSTNAME_ENV)
    if not host:
        die(
            message=(
                f"{PUBLIC_HOSTNAME_ENV} is not set; webapps routing requires it. "
                "This usually means the AppTemplate is not wired for env-bearer overlay."
            ),
            code=1,
        )
    return host


def route_block_webapp_host(slug: str, port: int, base_host: str) -> str:
    """Build a host-routed Caddy block for one user Web App."""
    name = matcher_name(slug=slug)
    return (
        f"@{name} header X-Forwarded-Host {slug}-{base_host}\n"
        f"handle @{name} {{\n"
        f"\treverse_proxy 127.0.0.1:{port} {{\n"
        f"\t\theader_up X-Forwarded-Host {{header.X-Forwarded-Host}}\n"
        f"\t}}\n"
        f"}}\n"
    )


def route_block_internal(slug: str, port: int, base_host: str) -> str:
    """Build a same-origin path route for one platform-internal Web App."""
    name = matcher_name(slug=slug)
    return (
        f"@{name}_root {{\n"
        f"\theader X-Forwarded-Host {base_host}\n"
        f"\tpath /webapps/{slug}\n"
        f"}}\n"
        f"redir @{name}_root /webapps/{slug}/ 308\n"
        f"@{name} {{\n"
        f"\theader X-Forwarded-Host {base_host}\n"
        f"\tpath /webapps/{slug}/*\n"
        f"}}\n"
        f"handle @{name} {{\n"
        f"\thandle_path /webapps/{slug}/* {{\n"
        f"\t\treverse_proxy 127.0.0.1:{port} {{\n"
        f"\t\t\theader_up X-Forwarded-Host {{header.X-Forwarded-Host}}\n"
        f"\t\t\theader_up X-Forwarded-Prefix /webapps/{slug}\n"
        f"\t\t}}\n"
        f"\t}}\n"
        f"}}\n"
    )


def regenerate_webapp_routes(document: dict) -> None:
    """Regenerate only the Web Apps Caddy fragment from owned workloads."""
    ensure_webapp_layout()
    base_host = public_hostname()
    blocks: list[str] = []
    for slug, entry in sorted(webapp_processes(document=document).items()):
        if entry.get("disabled"):
            continue
        port = port_from_entry(entry=entry)
        if port is None:
            continue
        if is_internal_slug(slug=slug):
            blocks.append(
                route_block_internal(slug=slug, port=port, base_host=base_host)
            )
        else:
            blocks.append(
                route_block_webapp_host(slug=slug, port=port, base_host=base_host)
            )
    WEBAPP_ROUTES_PATH.write_text(
        "".join(blocks) if blocks else "# no Web App routes\n",
        encoding="utf-8",
    )


def port_from_entry(entry: dict) -> int | None:
    """Read the Web App port declaration from one owned process entry."""
    for item in entry.get("environment", []):
        if isinstance(item, str) and item.startswith("WEBAPP_PORT="):
            return int(item.split("=", 1)[1])
    return None


def next_free_port(document: dict) -> int:
    """Allocate one port across every Web App and Widget workload."""
    try:
        return process_supervisor.next_free_port(document=document)
    except process_supervisor.ManagedPortError as exc:
        die(message=str(exc), code=1)


def is_routed(entry: dict) -> bool:
    """Return whether a Web App entry currently has a Caddy route."""
    return not entry.get("disabled") and port_from_entry(entry=entry) is not None


def make_process_entry(slug: str, command: str, cwd: str, port: int) -> dict:
    """Build one namespaced Web App process-compose entry."""
    return {
        "command": command,
        "working_dir": cwd,
        "log_location": str(WEBAPP_LOGS_DIR / f"{slug}.log"),
        "log_configuration": process_supervisor.plain_text_log_configuration(),
        "environment": [f"WEBAPP_PORT={port}"],
        "availability": {
            "restart": "on_failure",
            "backoff_seconds": 2,
            "max_restarts": 5,
        },
        "readiness_probe": {
            "exec": {
                "command": process_supervisor.readiness_probe_command(port=port),
            },
            "initial_delay_seconds": 5,
            "period_seconds": 10,
            "timeout_seconds": 2,
            "success_threshold": 1,
            "failure_threshold": 6,
        },
    }


def wait_for_ready(slug: str, timeout: int) -> dict:
    """Wait for one namespaced Web App process to become ready."""
    return process_supervisor.wait_for_process_ready(
        project=process_supervisor.APP_WORKLOADS_PROJECT,
        process_name=webapp_process_name(slug=slug),
        timeout=timeout,
    )


def url_for(slug: str) -> str:
    """Build the user-facing URL for one Web App."""
    host = os.environ.get(PUBLIC_HOSTNAME_ENV) or "<your-agent-hostname>"
    if is_internal_slug(slug=slug):
        return f"https://{host}/webapps/{slug}/"
    return f"https://{slug}-{host}/"
