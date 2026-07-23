"""Shared process-compose supervision for system processes and app workloads."""

from __future__ import annotations

import fcntl
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Literal, Self

import yaml


WorkloadKind = Literal["webapp", "widget"]

WEBAPP_WORKLOAD_KIND: WorkloadKind = "webapp"
WIDGET_WORKLOAD_KIND: WorkloadKind = "widget"
WORKLOAD_KINDS = frozenset({WEBAPP_WORKLOAD_KIND, WIDGET_WORKLOAD_KIND})

PORT_MIN = 4000
PORT_MAX = 4019
PROCESS_COMPOSE_ADDR = "127.0.0.1"
DEFAULT_TIMEOUT_SECONDS = 75
PROCESS_COMPOSE_TERMINAL_STATUSES = frozenset({"Completed", "Error", "Skipped"})
MANAGED_PORT_KEYS = frozenset({"WEBAPP_PORT", "WIDGET_PORT"})

SYSTEM_LOGS_DIR = Path("/workspace/.config/process-compose/system/logs")


@dataclass(frozen=True)
class ProcessComposeProject:
    config_dir: Path
    port: str
    lock_name: str
    required_dirs: tuple[Path, ...]

    @property
    def yaml_path(self) -> Path:
        return self.config_dir / "process-compose.yaml"

    @property
    def lock_file(self) -> Path:
        return self.config_dir / self.lock_name


APP_WORKLOADS_PROJECT = ProcessComposeProject(
    config_dir=Path("/workspace/.config/process-compose/app-workloads"),
    port="9957",
    lock_name=".app-workloads.lock",
    required_dirs=(),
)
SYSTEM_PROJECT = ProcessComposeProject(
    config_dir=Path("/workspace/.config/process-compose/system"),
    port="9956",
    lock_name=".system.lock",
    required_dirs=(SYSTEM_LOGS_DIR,),
)


class ManagedPortError(ValueError):
    """Report invalid or ambiguous ports in the app-workloads project."""


def ensure_process_compose_layout(project: ProcessComposeProject) -> None:
    """Create one process-compose project's durable layout when absent."""
    for path in (*project.required_dirs, project.config_dir):
        path.mkdir(parents=True, exist_ok=True)
    if not project.yaml_path.exists():
        project.yaml_path.write_text('version: "0.5"\nprocesses: {}\n', encoding="utf-8")


class ProcessComposeLock:
    """Serialize mutations to one process-compose project."""

    def __init__(self, project: ProcessComposeProject) -> None:
        self.project = project
        self._handle: IO[str] | None = None

    def __enter__(self) -> Self:
        ensure_process_compose_layout(project=self.project)
        self._handle = self.project.lock_file.open(mode="w", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        del exc_type, exc_value, traceback
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def load_process_compose_yaml(project: ProcessComposeProject) -> dict:
    """Load one process-compose document and ensure its required top-level keys."""
    raw = yaml.safe_load(project.yaml_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("version", "0.5")
    raw.setdefault("processes", {})
    return raw


def save_process_compose_yaml(project: ProcessComposeProject, document: dict) -> None:
    """Atomically replace one process-compose document."""
    temporary_path = project.yaml_path.with_suffix(".yaml.tmp")
    temporary_path.write_text(render_process_compose_yaml(document=document), encoding="utf-8")
    temporary_path.replace(project.yaml_path)


def render_process_compose_yaml(document: dict) -> str:
    """Serialize a process-compose document deterministically."""
    return yaml.safe_dump(document, sort_keys=False)


def run_process_compose(*args: str, project: ProcessComposeProject, check: bool, capture: bool) -> subprocess.CompletedProcess:
    """Run a command against one process-compose daemon."""
    command = [
        "process-compose",
        "--address",
        PROCESS_COMPOSE_ADDR,
        "--port",
        project.port,
        *args,
    ]
    return subprocess.run(
        command,
        check=check,
        capture_output=capture,
        text=True,
    )


def process_compose_project_update(project: ProcessComposeProject) -> None:
    """Reload a running process-compose project from its durable document."""
    run_process_compose(
        "project",
        "update",
        "--config",
        str(project.yaml_path),
        project=project,
        check=True,
        capture=False,
    )


def plain_text_log_configuration() -> dict:
    """Build process-compose's plain-text log configuration."""
    return {
        "disable_json": True,
        "no_metadata": True,
        "no_color": True,
        "fields_order": ["message"],
        "flush_each_line": True,
    }


def process_compose_states(project: ProcessComposeProject) -> list[dict]:
    """Read and validate every state from one process-compose daemon."""
    result = run_process_compose(
        "list",
        "-o",
        "json",
        project=project,
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"process-compose list failed: {result.stderr.strip() or 'unknown error'}"
        )
    try:
        states = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("process-compose returned invalid JSON state") from exc
    if not isinstance(states, list) or any(
        not isinstance(state, dict) for state in states
    ):
        raise RuntimeError("process-compose returned invalid process state")
    return states


def process_compose_state_for(project: ProcessComposeProject, process_name: str) -> dict | None:
    """Read one exact process state."""
    return next(
        (
            state
            for state in process_compose_states(project=project)
            if state.get("name") == process_name
        ),
        None,
    )


def wait_for_process_ready(project: ProcessComposeProject, process_name: str, timeout: int) -> dict:
    """Wait until one process is ready, terminal, or the timeout expires."""
    deadline = time.monotonic() + timeout
    last_state: dict | None = None
    while True:
        last_state = process_compose_state_for(
            project=project,
            process_name=process_name,
        )
        if last_state is not None:
            if last_state.get("is_ready") == "Ready":
                return last_state
            if process_compose_state_failed_before_ready(state=last_state):
                return last_state
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.5, remaining))
    return last_state or {
        "name": process_name,
        "status": "unknown",
        "is_ready": "Unknown",
    }


def workload_process_name(kind: WorkloadKind, slug: str) -> str:
    """Map a product slug into the shared supervisor namespace."""
    if kind not in WORKLOAD_KINDS:
        raise ValueError(f"unsupported workload kind: {kind!r}")
    return f"{kind}.{slug}"


def is_workload_process_name(name: str, kind: WorkloadKind) -> bool:
    """Return whether a process belongs to one workload kind."""
    return name.startswith(f"{kind}.")


def workloads_for_kind(document: dict, kind: WorkloadKind) -> dict[str, dict]:
    """Return one product's workload entries keyed by product slug."""
    prefix = f"{kind}."
    return {
        name.removeprefix(prefix): entry
        for name, entry in document.get("processes", {}).items()
        if isinstance(name, str)
        and isinstance(entry, dict)
        and name.startswith(prefix)
    }


def managed_port_from_entry(entry: dict, process_name: str) -> int | None:
    """Return one validated Web App or Widget port from a process entry."""
    environment = entry.get("environment", [])
    if not isinstance(environment, list):
        raise ManagedPortError(
            f"process {process_name!r} environment must be a list of strings"
        )

    declarations: list[tuple[str, str]] = []
    for index, item in enumerate(environment):
        if not isinstance(item, str):
            raise ManagedPortError(
                f"process {process_name!r} environment item {index} must be a string"
            )
        key, separator, value = item.partition("=")
        if key not in MANAGED_PORT_KEYS:
            continue
        if not separator or not value or re.fullmatch(r"[0-9]+", value) is None:
            raise ManagedPortError(
                f"process {process_name!r} has malformed {key} declaration"
            )
        declarations.append((key, value))

    if len(declarations) > 1:
        names = ", ".join(key for key, _value in declarations)
        raise ManagedPortError(
            f"process {process_name!r} has multiple managed port declarations: {names}"
        )
    if not declarations:
        return None

    key, value = declarations[0]
    port = int(value)
    if not PORT_MIN <= port <= PORT_MAX:
        raise ManagedPortError(
            f"process {process_name!r} has out-of-range {key}={port}; "
            f"expected {PORT_MIN}-{PORT_MAX}"
        )
    return port


def used_ports(document: dict) -> set[int]:
    """Return all uniquely assigned managed ports."""
    processes = document.get("processes", {})
    if not isinstance(processes, dict):
        raise ManagedPortError("process-compose processes must be a mapping")
    owners: dict[int, str] = {}
    for name, entry in processes.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ManagedPortError(
                "process-compose process entries must be named mappings"
            )
        port = managed_port_from_entry(entry=entry, process_name=name)
        if port is None:
            continue
        prior_owner = owners.get(port)
        if prior_owner is not None:
            raise ManagedPortError(
                f"managed port {port} is assigned to both "
                f"{prior_owner!r} and {name!r}"
            )
        owners[port] = name
    return set(owners)


def next_free_port(document: dict) -> int:
    """Return the first unassigned app-workload port."""
    assigned_ports = used_ports(document=document)
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in assigned_ports:
            return port
    raise ManagedPortError(
        f"no free ports in {PORT_MIN}-{PORT_MAX}; delete a Web App or Widget first"
    )


def readiness_probe_command(port: int) -> str:
    """Build the loopback TCP readiness probe shared by HTTP workloads."""
    return f"bash -c ': <> /dev/tcp/127.0.0.1/{port}'"


def process_compose_state_failed_before_ready(state: dict) -> bool:
    """Return whether process-compose reached a terminal non-ready state."""
    return (
        state.get("status") in PROCESS_COMPOSE_TERMINAL_STATUSES
        and state.get("is_ready") != "Ready"
    )
