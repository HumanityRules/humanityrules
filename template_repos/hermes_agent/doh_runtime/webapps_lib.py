"""Shared helpers for the webapps mechanism.

Imported by both the `webapps` CLI and the `__admin` webapp. Webapp source of
truth is /workspace/.config/process-compose/webapps/process-compose.yaml;
routes.caddy is regenerated from it on every mutation. See
docs/webapps_design.md.

User webapps live at <slug>.<agent-host> (Host-based Caddy routing). The
per-agent ALB wildcard cert + Route 53 wildcard record + listener-rule host
condition that make those URLs resolve are provisioned at agent-deploy time
when the AppTemplate sets `enable_subhosting=True`.

Platform-internal slugs (those starting with `__`, e.g. `__admin`) stay at
<agent-host>/webapps/<slug>/ so the WebUI's same-origin extension can call
their APIs without CORS surgery.

`webapps/` holds user-facing artifacts (their projects, their logs);
`.config/` holds DOH supervision config (the process-compose YAML, the caddy
routes). Both live under $HOME=/workspace so the sandbox owns them.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ProcessComposeProject:
    name: str
    config_dir: Path
    port: str
    lock_name: str
    required_dirs: tuple[Path, ...]
    route_file: Path | None

    @property
    def yaml_path(self) -> Path:
        return self.config_dir / "process-compose.yaml"

    @property
    def lock_file(self) -> Path:
        return self.config_dir / self.lock_name


LOGS_DIR = Path("/workspace/webapps/logs")
SYSTEM_SLUG_PREFIX = "system."

PORT_MIN = 4000
PORT_MAX = 4019
PROCESS_COMPOSE_ADDR = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 90
WEBAPPS_PROJECT = ProcessComposeProject(
    name="webapps",
    config_dir=Path("/workspace/.config/process-compose/webapps"),
    port="9957",
    lock_name=".webapps.lock",
    required_dirs=(
        Path("/workspace/webapps"),
        Path("/workspace/webapps/projects"),
        Path("/workspace/.config/caddy"),
        LOGS_DIR,
    ),
    route_file=Path("/workspace/.config/caddy/routes.caddy"),
)
SYSTEM_PROJECT = ProcessComposeProject(
    name="system",
    config_dir=Path("/workspace/.config/process-compose/system"),
    port="9956",
    lock_name=".system.lock",
    required_dirs=(LOGS_DIR,),
    route_file=None,
)
# Optional `__` prefix marks platform-internal slugs (e.g. __admin). No
# enforcement: bootstrap wins the cold-start race; agent attempts collide.
# Internal slugs are routed by path (<host>/webapps/<slug>/); user slugs are
# routed by host (<slug>.<host>/). See route generation below.
SLUG_PATTERN = re.compile(r"^(?:__)?[a-z][a-z0-9-]{0,30}[a-z0-9]$")
INTERNAL_SLUG_PREFIX = "__"
PUBLIC_HOSTNAME_ENV = "DOH_PUBLIC_HOSTNAME"


def die(msg: str, code: int = 1) -> None:
    print(f"webapps: {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_process_compose_layout(project: ProcessComposeProject) -> None:
    for path in (*project.required_dirs, project.config_dir):
        path.mkdir(parents=True, exist_ok=True)
    if not project.yaml_path.exists():
        project.yaml_path.write_text('version: "0.5"\nprocesses: {}\n')
    if project.route_file is not None and not project.route_file.exists():
        project.route_file.write_text("# no routes\n")


class ProcessComposeLock:
    def __init__(self, project: ProcessComposeProject) -> None:
        self.project = project

    def __enter__(self) -> "ProcessComposeLock":
        ensure_process_compose_layout(project=self.project)
        self.fh = open(self.project.lock_file, "w")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: object) -> None:
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()


def load_process_compose_yaml(project: ProcessComposeProject) -> dict:
    raw = yaml.safe_load(project.yaml_path.read_text()) or {}
    raw.setdefault("version", "0.5")
    raw.setdefault("processes", {})
    return raw


def save_process_compose_yaml(project: ProcessComposeProject, doc: dict) -> None:
    tmp = project.yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False))
    tmp.replace(project.yaml_path)


def run_process_compose(*args: str, project: ProcessComposeProject, check: bool, capture: bool) -> subprocess.CompletedProcess:
    cmd = [
        "process-compose",
        "--address", PROCESS_COMPOSE_ADDR,
        "--port", project.port,
        *args,
    ]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def process_compose_project_update(project: ProcessComposeProject) -> None:
    run_process_compose("project", "update", "--config", str(project.yaml_path), project=project, check=True, capture=False)


def process_compose_states(project: ProcessComposeProject) -> list[dict]:
    res = run_process_compose("list", "-o", "json", project=project, check=False, capture=True)
    if res.returncode != 0:
        die(f"process-compose list failed: {res.stderr.strip()}")
    return json.loads(res.stdout or "[]")


def process_compose_state_for(project: ProcessComposeProject, slug: str) -> dict | None:
    return next((s for s in process_compose_states(project=project) if s.get("name") == slug), None)


def port_from_entry(entry: dict) -> int | None:
    for item in entry.get("environment", []):
        if item.startswith("WEBAPP_PORT="):
            return int(item.split("=", 1)[1])
    return None


def used_ports(doc: dict) -> set[int]:
    ports: set[int] = set()
    for entry in doc.get("processes", {}).values():
        port = port_from_entry(entry)
        if port is not None:
            ports.add(port)
    return ports


def next_free_port(doc: dict) -> int:
    used = used_ports(doc)
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in used:
            return port
    die(f"no free ports in {PORT_MIN}-{PORT_MAX}; delete an app first")


def validate_slug(slug: str) -> None:
    if slug.startswith(SYSTEM_SLUG_PREFIX):
        die(
            f"invalid slug {slug!r}: the {SYSTEM_SLUG_PREFIX!r} prefix is reserved "
            "for DOH-managed system processes; pick another name"
        )
    if not SLUG_PATTERN.match(slug):
        die(
            f"invalid slug {slug!r}: lowercase letters/digits/hyphens, "
            "2-32 chars, must start with letter and end alphanumeric "
            "(optional `__` prefix reserved for platform internals)"
        )


def is_internal_slug(slug: str) -> bool:
    """Platform-internal slug (`__admin`, future runtime-admin webapps)."""
    return slug.startswith(INTERNAL_SLUG_PREFIX)


def matcher_name(slug: str) -> str:
    """Caddy named-matcher token for *slug*; sanitized for valid Caddyfile syntax."""
    return "webapp_" + slug.replace("-", "_").lstrip("_")


def public_hostname() -> str:
    """Read the agent's public hostname from the env-bearer overlay var.

    `DOH_PUBLIC_HOSTNAME` is allow-listed in the nono profile and injected
    by CDK at task-definition build time (see deploy_app.py).
    """
    host = os.environ.get(PUBLIC_HOSTNAME_ENV)
    if not host:
        die(
            f"{PUBLIC_HOSTNAME_ENV} is not set; webapps routing requires it. "
            "This usually means the AppTemplate isn't wired for env-bearer overlay.",
        )
    return host


def route_block_subhost(slug: str, port: int, base_host: str) -> str:
    """Caddy site-matcher block for a user webapp at <slug>.<base-host>.

    Per-app wildcard cert + DNS + ALB host condition (provisioned at
    agent-deploy time when enable_subhosting=True) make these reachable
    end-to-end without per-webapp infra work.

    Matches on X-Forwarded-Host, not Host: policy-proxy strips Host (httpx
    rewrites it to the upstream's 127.0.0.1:8787) and copies the original
    value into X-Forwarded-Host before forwarding to Caddy.

    The `header_up X-Forwarded-Host` line propagates policy-proxy's value
    on to the user webapp. Caddy's reverse_proxy default for that header
    is "set from the inbound Host", which here would mean 127.0.0.1:8787 —
    clobbering the public hostname before any link helper, OpenAPI server
    URL, OAuth callback, or redirect could see it.
    """
    name = matcher_name(slug)
    return (
        f"@{name} header X-Forwarded-Host {slug}.{base_host}\n"
        f"handle @{name} {{\n"
        f"\treverse_proxy 127.0.0.1:{port} {{\n"
        f"\t\theader_up X-Forwarded-Host {{header.X-Forwarded-Host}}\n"
        f"\t}}\n"
        f"}}\n"
    )


def route_block_internal(slug: str, port: int, base_host: str) -> str:
    """Caddy block for a platform-internal slug at <base-host>/webapps/<slug>/.

    Stays path-based on the bare host so the WebUI extension can call its API
    same-origin. X-Forwarded-Prefix gives the upstream the public base path
    if it needs to generate absolute URLs.

    Matches on X-Forwarded-Host (not Host) for the same reason as
    route_block_subhost — policy-proxy rewrites Host on the way in.
    """
    name = matcher_name(slug)
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


def regenerate_routes(doc: dict) -> None:
    base_host = public_hostname()
    blocks: list[str] = []
    for slug, entry in sorted(doc.get("processes", {}).items()):
        if entry.get("disabled"):
            continue
        port = port_from_entry(entry)
        if port is None:
            continue
        if is_internal_slug(slug):
            blocks.append(route_block_internal(slug=slug, port=port, base_host=base_host))
        else:
            blocks.append(route_block_subhost(slug=slug, port=port, base_host=base_host))
    route_file = WEBAPPS_PROJECT.route_file
    if route_file is None:
        die("webapps project is missing a Caddy routes file")
    route_file.write_text("".join(blocks) if blocks else "# no routes\n")


def is_routed(entry: dict) -> bool:
    return not entry.get("disabled") and port_from_entry(entry) is not None


def make_process_entry(slug: str, command: str, cwd: str, port: int) -> dict:
    return {
        "command": command,
        "working_dir": cwd,
        "log_location": str(LOGS_DIR / f"{slug}.log"),
        "environment": [f"WEBAPP_PORT={port}"],
        "availability": {
            "restart": "on_failure",
            "backoff_seconds": 2,
            "max_restarts": 5,
        },
        "readiness_probe": {
            "exec": {
                "command": f"bash -c 'echo > /dev/tcp/127.0.0.1/{port}'",
            },
            "initial_delay_seconds": 1,
            "period_seconds": 2,
            "timeout_seconds": 2,
            "success_threshold": 1,
            "failure_threshold": 1,
        },
    }


def wait_for_ready(slug: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = process_compose_state_for(project=WEBAPPS_PROJECT, slug=slug)
        if state is None:
            time.sleep(0.5)
            continue
        if state.get("is_ready") == "Ready":
            return state
        if state.get("status") == "Error":
            return state
        time.sleep(0.5)
    return process_compose_state_for(project=WEBAPPS_PROJECT, slug=slug) or {
        "name": slug,
        "status": "unknown",
        "is_ready": "Unknown",
    }


def url_for(slug: str) -> str:
    """User-facing URL for a webapp.

    User slugs live at <slug>.<agent-host>; platform-internal slugs (`__*`)
    stay at <agent-host>/webapps/<slug>/ so the WebUI's same-origin extension
    can reach them without CORS.
    """
    host = os.environ.get(PUBLIC_HOSTNAME_ENV) or "<your-agent-hostname>"
    if is_internal_slug(slug):
        return f"https://{host}/webapps/{slug}/"
    return f"https://{slug}.{host}/"
