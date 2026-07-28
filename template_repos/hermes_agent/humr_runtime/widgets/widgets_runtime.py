"""Routing and lifecycle reconciliation for HumR Widgets."""

from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import stat
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "process_supervisor"))

import process_supervisor
import widgets_core


WIDGETS_CADDY_PATH = Path("/workspace/.config/caddy/widgets.caddy")
WIDGETS_STATIC_ROOT = widgets_core.WIDGETS_CONFIG_ROOT / "static"
WIDGETS_TOMBSTONES_ROOT = widgets_core.WIDGETS_ROOT.parent / ".widgets-tombstones"
WIDGETS_LOGS_ROOT = widgets_core.WIDGETS_CONFIG_ROOT / "logs"
WIDGET_UNAVAILABLE_PATH = Path("/opt/humr/runtime/widgets/widget-unavailable.html")
DEFAULT_APPLY_TIMEOUT_SECONDS = process_supervisor.DEFAULT_TIMEOUT_SECONDS

_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


@dataclass(frozen=True)
class WidgetRuntimePaths:
    widgets_root: Path
    registry_path: Path
    routes_path: Path
    static_root: Path
    tombstones_root: Path
    logs_root: Path
    unavailable_path: Path
    process_project: process_supervisor.ProcessComposeProject
    lock_path: Path


@dataclass(frozen=True)
class WidgetReconcileResult:
    widgets: tuple[widgets_core.WidgetManifest, ...]
    failures: tuple[widgets_core.WidgetValidationFailure, ...]


@dataclass(frozen=True)
class _PublicationArtifact:
    staged_path: Path
    destination_path: Path


@dataclass
class _PublicationState:
    artifact: _PublicationArtifact
    backup_path: Path
    backup_moved: bool
    staged_moved: bool


class WidgetReconciliationError(RuntimeError):
    """Report a reconciliation-wide failure that must preserve derived state."""


class WidgetDeletionCommittedError(WidgetReconciliationError):
    """Report committed deletion whose isolated residuals still need cleanup."""


DEFAULT_PATHS = WidgetRuntimePaths(
    widgets_root=widgets_core.WIDGETS_ROOT,
    registry_path=widgets_core.REGISTRY_PATH,
    routes_path=WIDGETS_CADDY_PATH,
    static_root=WIDGETS_STATIC_ROOT,
    tombstones_root=WIDGETS_TOMBSTONES_ROOT,
    logs_root=WIDGETS_LOGS_ROOT,
    unavailable_path=WIDGET_UNAVAILABLE_PATH,
    process_project=process_supervisor.APP_WORKLOADS_PROJECT,
    lock_path=widgets_core.LOCK_PATH,
)


def _widget_process_name(slug: str) -> str:
    """Map a Widget slug into the shared supervisor namespace."""
    return process_supervisor.workload_process_name(
        kind=process_supervisor.WIDGET_WORKLOAD_KIND,
        slug=slug,
    )


def build_widgets_caddy_fragment(
    widgets: Iterable[widgets_core.WidgetManifest],
    static_root: Path,
    registry_path: Path,
    backend_ports: Mapping[str, int],
    unavailable_path: Path,
) -> str:
    """Render deterministic same-origin registry, static, and backend routes."""
    blocks = [_registry_route_block(registry_path=registry_path)]
    for widget in sorted(widgets, key=lambda item: item.slug):
        blocks.append(
            _widget_route_block(
                widget=widget,
                port=backend_ports.get(widget.slug),
                static_root=static_root,
                unavailable_path=unavailable_path,
            )
        )
    if backend_ports:
        blocks.append(_widget_backend_error_routes(unavailable_path=unavailable_path))
    return "\n".join(blocks)


def reconcile_widgets(paths: WidgetRuntimePaths, target_slug: str | None, update_processes: bool) -> WidgetReconcileResult:
    """Rebuild registry, static snapshots, and routes from authoritative manifests."""
    paths.widgets_root.mkdir(parents=True, exist_ok=True)
    paths.logs_root.mkdir(parents=True, exist_ok=True)
    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        with process_supervisor.ProcessComposeLock(project=paths.process_project):
            return _reconcile_widgets_unlocked(
                paths=paths,
                target_slug=target_slug,
                update_processes=update_processes,
            )


def list_widgets(paths: WidgetRuntimePaths) -> widgets_core.WidgetDiscovery:
    """Read the authoritative manifests without consulting generated registry state."""
    paths.widgets_root.mkdir(parents=True, exist_ok=True)
    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        return widgets_core.discover_widgets(widgets_root=paths.widgets_root)


def wait_for_widget_ready(
    paths: WidgetRuntimePaths,
    slug: str,
    timeout: int,
) -> dict[str, Any]:
    """Wait for one reconciled Widget backend's namespaced process."""
    process_name = _widget_process_name(slug=slug)
    state = process_supervisor.wait_for_process_ready(
        project=paths.process_project,
        process_name=process_name,
        timeout=timeout,
    )
    if not isinstance(state, dict):
        raise WidgetReconciliationError(
            f"process-compose returned invalid state for {process_name!r}"
        )
    return state


def delete_widget(paths: WidgetRuntimePaths, slug: str, confirmed: bool, update_processes: bool) -> WidgetReconcileResult:
    """Permanently remove exactly one Widget after reversible reconciliation."""
    if not confirmed:
        raise widgets_core.WidgetValidationError(
            slug=slug, message="delete requires --yes because deletion is permanent"
        )
    widgets_core.validate_slug(slug=slug)
    paths.widgets_root.mkdir(parents=True, exist_ok=True)
    paths.logs_root.mkdir(parents=True, exist_ok=True)

    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        with process_supervisor.ProcessComposeLock(project=paths.process_project):
            paths.tombstones_root.mkdir(parents=True, exist_ok=True)
            _require_tombstones_outside_discovery(
                widgets_root=paths.widgets_root,
                tombstones_root=paths.tombstones_root,
                slug=slug,
            )
            tombstone_path = paths.tombstones_root / slug
            widget_path = paths.widgets_root / slug
            if _path_exists(path=tombstone_path):
                if _path_exists(path=widget_path):
                    raise WidgetReconciliationError(
                        f"Widget {slug!r} has both source and deletion residuals; "
                        "refusing ambiguous cleanup"
                    )
                result = _reconcile_widgets_unlocked(
                    paths=paths,
                    target_slug=None,
                    update_processes=update_processes,
                )
                _finish_committed_delete_cleanup(
                    tombstone_path=tombstone_path,
                    slug=slug,
                )
                return result

            widget_path = _validated_delete_path(
                widgets_root=paths.widgets_root,
                slug=slug,
            )
            if widget_path.parent.stat().st_dev != paths.tombstones_root.stat().st_dev:
                raise widgets_core.WidgetValidationError(
                    slug=slug, message="Widget source and deletion tombstone are not on the same filesystem"
                )
            log_path = paths.logs_root / f"{slug}.log"
            if (
                _path_exists(path=log_path)
                and log_path.parent.stat().st_dev
                != paths.tombstones_root.stat().st_dev
            ):
                raise widgets_core.WidgetValidationError(
                    slug=slug,
                    message=(
                        "Widget log and deletion tombstone are not on the same "
                        "filesystem"
                    ),
                )
            tombstone_path.mkdir()
            source_tombstone = tombstone_path / "source"
            log_tombstone = tombstone_path / "backend.log"
            try:
                _move_delete_artifacts_to_tombstone(
                    widget_path=widget_path,
                    log_path=log_path,
                    source_tombstone=source_tombstone,
                    log_tombstone=log_tombstone,
                )
            except BaseException:
                _restore_delete_artifacts(
                    widget_path=widget_path,
                    log_path=log_path,
                    source_tombstone=source_tombstone,
                    log_tombstone=log_tombstone,
                    slug=slug,
                )
                raise
            try:
                result = _reconcile_widgets_unlocked(
                    paths=paths,
                    target_slug=None,
                    update_processes=update_processes,
                )
            except BaseException:
                _restore_delete_artifacts(
                    widget_path=widget_path,
                    log_path=log_path,
                    source_tombstone=source_tombstone,
                    log_tombstone=log_tombstone,
                    slug=slug,
                )
                raise
            _finish_committed_delete_cleanup(
                tombstone_path=tombstone_path,
                slug=slug,
            )
            return result


def reconcile_widget_processes(
    document: dict[str, Any],
    widgets: Iterable[widgets_core.WidgetManifest],
    widgets_root: Path,
    logs_root: Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Replace only Widget-owned supervisor entries and allocate stable shared ports."""
    reconciled = copy.deepcopy(document)
    raw_processes = reconciled.get("processes", {})
    if not isinstance(raw_processes, dict):
        raise WidgetReconciliationError("process-compose processes must be a mapping")
    processes: dict[str, Any] = raw_processes
    backend_widgets = tuple(
        sorted(
            (widget for widget in widgets if widget.backend is not None),
            key=lambda widget: widget.slug,
        )
    )

    used_ports: set[int] = set()
    preserved_port_owners: dict[int, str] = {}
    preserved_processes: dict[str, Any] = {}
    for name, entry in processes.items():
        if process_supervisor.is_workload_process_name(
            name=name,
            kind=process_supervisor.WIDGET_WORKLOAD_KIND,
        ):
            continue
        preserved_processes[name] = entry
        port = _managed_port(entry=entry, process_name=name)
        if port is not None:
            prior_owner = preserved_port_owners.get(port)
            if prior_owner is not None:
                raise WidgetReconciliationError(
                    f"managed port {port} is assigned to both "
                    f"{prior_owner!r} and {name!r}"
                )
            preserved_port_owners[port] = name
            used_ports.add(port)

    backend_ports: dict[str, int] = {}
    for widget in backend_widgets:
        process_name = _widget_process_name(slug=widget.slug)
        existing_entry = processes.get(process_name)
        existing_port = _widget_port(
            entry=existing_entry,
            process_name=process_name,
        )
        if (
            existing_port is not None
            and process_supervisor.PORT_MIN
            <= existing_port
            <= process_supervisor.PORT_MAX
            and existing_port not in used_ports
        ):
            backend_ports[widget.slug] = existing_port
            used_ports.add(existing_port)

    for widget in backend_widgets:
        if widget.slug in backend_ports:
            continue
        port = _next_widget_port(used_ports=used_ports)
        backend_ports[widget.slug] = port
        used_ports.add(port)

    for widget in backend_widgets:
        process_name = _widget_process_name(slug=widget.slug)
        preserved_processes[process_name] = make_widget_process_entry(
            widget=widget,
            widgets_root=widgets_root,
            logs_root=logs_root,
            port=backend_ports[widget.slug],
        )
    reconciled["processes"] = preserved_processes
    return reconciled, backend_ports


def make_widget_process_entry(
    widget: widgets_core.WidgetManifest, widgets_root: Path, logs_root: Path, port: int
) -> dict[str, Any]:
    """Build one declarative Widget backend process-compose entry."""
    if widget.backend is None:
        raise ValueError(f"Widget {widget.slug!r} has no backend")
    return {
        "command": widget.backend.command,
        "working_dir": str(widgets_root / widget.slug),
        "log_location": str(logs_root / f"{widget.slug}.log"),
        "log_configuration": process_supervisor.plain_text_log_configuration(),
        "environment": [
            f"WIDGET_SLUG={widget.slug}",
            f"WIDGET_BASE_PATH=/widgets/{widget.slug}",
            f"WIDGET_PORT={port}",
        ],
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


def _managed_port(entry: object, process_name: str) -> int | None:
    if not isinstance(entry, dict):
        raise WidgetReconciliationError(
            f"process-compose entry {process_name!r} must be a mapping"
        )
    try:
        port = process_supervisor.managed_port_from_entry(
            entry=entry,
            process_name=process_name,
        )
    except (TypeError, ValueError) as exc:
        raise WidgetReconciliationError(
            f"process-compose entry {process_name!r} has an invalid managed port: {exc}"
        ) from exc
    if port is None or isinstance(port, int):
        return port
    raise WidgetReconciliationError(
        f"process-compose entry {process_name!r} has an invalid managed port"
    )


def _widget_port(entry: object, process_name: str) -> int | None:
    if entry is None:
        return None
    port = _managed_port(entry=entry, process_name=process_name)
    if port is None:
        return None
    if not isinstance(entry, dict):
        raise WidgetReconciliationError(
            f"process-compose entry {process_name!r} must be a mapping"
        )
    if any(
        isinstance(item, str) and item.partition("=")[0] == "WIDGET_PORT"
        for item in entry.get("environment", [])
    ):
        return port
    return None


def _next_widget_port(used_ports: set[int]) -> int:
    for port in range(
        process_supervisor.PORT_MIN,
        process_supervisor.PORT_MAX + 1,
    ):
        if port not in used_ports:
            return port
    raise WidgetReconciliationError(
        f"no free ports in {process_supervisor.PORT_MIN}-"
        f"{process_supervisor.PORT_MAX}; "
        "delete a Web App or Widget first"
    )


def _reconcile_widgets_unlocked(
    paths: WidgetRuntimePaths, target_slug: str | None, update_processes: bool
) -> WidgetReconcileResult:
    if target_slug is not None:
        widgets_core.load_widget(widgets_root=paths.widgets_root, slug=target_slug)

    discovery = widgets_core.discover_widgets(widgets_root=paths.widgets_root)
    root_failures = tuple(
        failure
        for failure in discovery.failures
        if failure.slug == str(paths.widgets_root)
    )
    if root_failures:
        raise WidgetReconciliationError(root_failures[0].message)

    staged_snapshot = _new_staging_path(destination_path=paths.static_root)
    staged_registry: Path | None = None
    staged_routes: Path | None = None
    staged_process_yaml: Path | None = None
    try:
        staged_snapshot.mkdir(parents=True)
        publishable_widgets, snapshot_failures = _build_static_snapshot(
            widgets=discovery.widgets,
            widgets_root=paths.widgets_root,
            staged_snapshot=staged_snapshot,
            target_slug=target_slug,
        )
        failures = tuple(
            sorted(
                (*discovery.failures, *snapshot_failures),
                key=lambda failure: failure.slug,
            )
        )
        process_document = process_supervisor.load_process_compose_yaml(
            project=paths.process_project
        )
        reconciled_process_document, backend_ports = reconcile_widget_processes(
            document=process_document,
            widgets=publishable_widgets,
            widgets_root=paths.widgets_root,
            logs_root=paths.logs_root,
        )
        registry_payload = widgets_core.build_registry_payload(widgets=publishable_widgets)
        registry_document = _render_registry_json(payload=registry_payload)
        routes_document = build_widgets_caddy_fragment(
            widgets=publishable_widgets,
            static_root=paths.static_root,
            registry_path=paths.registry_path,
            backend_ports=backend_ports,
            unavailable_path=paths.unavailable_path,
        )
        staged_registry = _stage_text(
            destination_path=paths.registry_path, document=registry_document
        )
        staged_routes = _stage_text(
            destination_path=paths.routes_path, document=routes_document
        )
        staged_process_yaml = _stage_text(
            destination_path=paths.process_project.yaml_path,
            document=process_supervisor.render_process_compose_yaml(
                document=reconciled_process_document
            ),
        )
        publish_hook: Callable[[], None] | None = None
        rollback_hook: Callable[[], None] | None = None
        if update_processes:
            def reload_process_project() -> None:
                process_supervisor.process_compose_project_update(
                    project=paths.process_project
                )

            publish_hook = reload_process_project
            rollback_hook = reload_process_project
        _publish_artifacts(
            artifacts=(
                _PublicationArtifact(
                    staged_path=staged_snapshot,
                    destination_path=paths.static_root,
                ),
                _PublicationArtifact(
                    staged_path=staged_registry,
                    destination_path=paths.registry_path,
                ),
                _PublicationArtifact(
                    staged_path=staged_routes,
                    destination_path=paths.routes_path,
                ),
                _PublicationArtifact(
                    staged_path=staged_process_yaml,
                    destination_path=paths.process_project.yaml_path,
                ),
            ),
            publish_hook=publish_hook,
            rollback_hook=rollback_hook,
        )
    finally:
        for staged_path in (
            staged_snapshot,
            staged_registry,
            staged_routes,
            staged_process_yaml,
        ):
            if staged_path is not None:
                _remove_path_best_effort(path=staged_path)

    return WidgetReconcileResult(
        widgets=publishable_widgets,
        failures=failures,
    )


def _build_static_snapshot(
    widgets: tuple[widgets_core.WidgetManifest, ...],
    widgets_root: Path,
    staged_snapshot: Path,
    target_slug: str | None,
) -> tuple[tuple[widgets_core.WidgetManifest, ...], tuple[widgets_core.WidgetValidationFailure, ...]]:
    publishable: list[widgets_core.WidgetManifest] = []
    failures: list[widgets_core.WidgetValidationFailure] = []
    for widget in widgets:
        if widget.frontend.mode != "static":
            publishable.append(widget)
            continue
        try:
            _snapshot_static_widget(
                widget=widget,
                widgets_root=widgets_root,
                staged_snapshot=staged_snapshot,
            )
        except widgets_core.WidgetValidationError as exc:
            _remove_path(path=staged_snapshot / widget.slug)
            if widget.slug == target_slug:
                raise
            failures.append(
                widgets_core.WidgetValidationFailure(
                    slug=widget.slug,
                    message=exc.message,
                )
            )
            continue
        publishable.append(widget)
    return tuple(publishable), tuple(failures)


def _snapshot_static_widget(widget: widgets_core.WidgetManifest, widgets_root: Path, staged_snapshot: Path) -> None:
    entry = widget.frontend.entry
    if entry is None:
        raise widgets_core.WidgetValidationError(
            slug=widget.slug, message="static Widget has no frontend entry"
        )
    entry_parts = PurePosixPath(entry).parts
    destination_root = staged_snapshot / widget.slug
    destination_root.mkdir()

    opened_descriptors: list[int] = []
    try:
        widgets_fd = _open_directory_path(path=widgets_root, slug=widget.slug)
        opened_descriptors.append(widgets_fd)
        widget_fd = _open_directory_at(
            parent_fd=widgets_fd,
            name=widget.slug,
            slug=widget.slug,
            description="Widget directory",
        )
        opened_descriptors.append(widget_fd)
        entry_parent_fd = widget_fd
        for component in entry_parts[:-1]:
            entry_parent_fd = _open_directory_at(
                parent_fd=entry_parent_fd,
                name=component,
                slug=widget.slug,
                description=f"static entry parent {component!r}",
            )
            opened_descriptors.append(entry_parent_fd)

        entry_fd = _open_regular_file_at(
            parent_fd=entry_parent_fd,
            name=entry_parts[-1],
            slug=widget.slug,
            description="static frontend entry",
        )
        os.close(entry_fd)

        _copy_static_directory(
            source_fd=entry_parent_fd,
            destination_dir=destination_root,
            slug=widget.slug,
            relative_dir=PurePosixPath("."),
        )
    finally:
        for descriptor in reversed(opened_descriptors):
            os.close(descriptor)


def _copy_static_directory(source_fd: int, destination_dir: Path, slug: str, relative_dir: PurePosixPath) -> None:
    try:
        names = sorted(os.listdir(source_fd))
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot list static frontend directory {relative_dir}: {exc}"
        ) from exc
    for name in names:
        relative_path = relative_dir / name
        try:
            source_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"cannot inspect static frontend path {relative_path}: {exc}"
            ) from exc
        if stat.S_ISLNK(source_stat.st_mode):
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"static frontend path {relative_path} must not be a symbolic link"
            )
        if stat.S_ISDIR(source_stat.st_mode):
            destination_child = destination_dir / name
            destination_child.mkdir()
            child_fd = _open_directory_at(
                parent_fd=source_fd,
                name=name,
                slug=slug,
                description=f"static frontend directory {relative_path}",
            )
            try:
                _copy_static_directory(
                    source_fd=child_fd,
                    destination_dir=destination_child,
                    slug=slug,
                    relative_dir=relative_path,
                )
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(source_stat.st_mode):
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"static frontend path {relative_path} must be a regular file or directory"
            )
        source_file_fd = _open_regular_file_at(
            parent_fd=source_fd,
            name=name,
            slug=slug,
            description=f"static frontend file {relative_path}",
        )
        try:
            _copy_regular_file(
                source_fd=source_file_fd,
                destination_path=destination_dir / name,
            )
        finally:
            os.close(source_file_fd)


def _open_directory_path(path: Path, slug: str) -> int:
    try:
        descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot open directory {path} without following links: {exc}"
        ) from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"path {path} is not a directory"
        )
    return descriptor


def _open_directory_at(parent_fd: int, name: str, slug: str, description: str) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot open {description} without following links: {exc}"
        ) from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"{description} is not a directory"
        )
    return descriptor


def _open_regular_file_at(parent_fd: int, name: str, slug: str, description: str) -> int:
    try:
        descriptor = os.open(name, _FILE_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot open {description} without following links: {exc}"
        ) from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"{description} is not a regular file"
        )
    return descriptor


def _copy_regular_file(source_fd: int, destination_path: Path) -> None:
    with (
        os.fdopen(os.dup(source_fd), mode="rb") as source_handle,
        destination_path.open(mode="xb") as destination_handle,
    ):
        shutil.copyfileobj(fsrc=source_handle, fdst=destination_handle)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())


def _move_delete_artifacts_to_tombstone(
    widget_path: Path,
    log_path: Path,
    source_tombstone: Path,
    log_tombstone: Path,
) -> None:
    os.replace(src=widget_path, dst=source_tombstone)
    if _path_exists(path=log_path):
        os.replace(src=log_path, dst=log_tombstone)


def _restore_delete_artifacts(
    widget_path: Path,
    log_path: Path,
    source_tombstone: Path,
    log_tombstone: Path,
    slug: str,
) -> None:
    restore_errors: list[OSError] = []
    for tombstone, destination in (
        (source_tombstone, widget_path),
        (log_tombstone, log_path),
    ):
        if not _path_exists(path=tombstone):
            continue
        try:
            if _path_exists(path=destination):
                raise OSError(f"restore destination already exists: {destination}")
            os.replace(src=tombstone, dst=destination)
        except OSError as exc:
            restore_errors.append(exc)
    tombstone_path = source_tombstone.parent
    try:
        tombstone_path.rmdir()
    except OSError as exc:
        if _path_exists(path=tombstone_path):
            restore_errors.append(exc)
    if restore_errors:
        details = "; ".join(str(error) for error in restore_errors)
        raise WidgetReconciliationError(
            f"failed to restore Widget {slug!r} deletion artifacts: {details}"
        ) from restore_errors[0]


def _finish_committed_delete_cleanup(tombstone_path: Path, slug: str) -> None:
    if tombstone_path.is_symlink() or not tombstone_path.is_dir():
        raise WidgetDeletionCommittedError(
            f"Widget {slug!r} deletion is committed, but its cleanup path "
            f"{tombstone_path} is not a safe directory"
        )
    try:
        shutil.rmtree(tombstone_path)
    except OSError as exc:
        raise WidgetDeletionCommittedError(
            f"Widget {slug!r} deletion is committed with residual cleanup at "
            f"{tombstone_path}; repeat `widgets delete {slug} --yes` to finish"
        ) from exc


def _validated_delete_path(widgets_root: Path, slug: str) -> Path:
    widget_path = widgets_root / slug
    if widget_path.is_symlink():
        raise widgets_core.WidgetValidationError(
            slug=slug, message="refusing to delete a symbolic-link Widget directory"
        )
    try:
        resolved_root = widgets_root.resolve(strict=True)
        resolved_widget = widget_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"Widget directory {widget_path} is unavailable: {exc}"
        ) from exc
    try:
        resolved_widget.relative_to(resolved_root)
    except ValueError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"Widget directory escapes {resolved_root}"
        ) from exc
    if not resolved_widget.is_dir():
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"Widget path {widget_path} is not a directory"
        )
    return widget_path


def _require_tombstones_outside_discovery(widgets_root: Path, tombstones_root: Path, slug: str) -> None:
    resolved_widgets_root = widgets_root.resolve(strict=True)
    resolved_tombstones_root = tombstones_root.resolve(strict=True)
    if resolved_tombstones_root == resolved_widgets_root:
        raise widgets_core.WidgetValidationError(
            slug=slug, message="deletion tombstones must be outside the Widgets root"
        )
    try:
        resolved_tombstones_root.relative_to(resolved_widgets_root)
    except ValueError:
        return
    raise widgets_core.WidgetValidationError(
        slug=slug, message="deletion tombstones must be outside the Widgets root"
    )


def _render_registry_json(payload: widgets_core.RegistryPayload) -> str:
    return f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"


def _stage_text(destination_path: Path, document: str) -> Path:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path = _new_staging_path(destination_path=destination_path)
    try:
        with staged_path.open(mode="x", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return staged_path


def _new_staging_path(destination_path: Path) -> Path:
    token = secrets.token_hex(8)
    return destination_path.with_name(
        f".{destination_path.name}.{os.getpid()}.{token}.stage"
    )


def _publish_artifacts(
    artifacts: tuple[_PublicationArtifact, ...],
    publish_hook: Callable[[], None] | None,
    rollback_hook: Callable[[], None] | None,
) -> None:
    transaction_token = secrets.token_hex(8)
    states: list[_PublicationState] = []
    publish_hook_started = False
    try:
        for artifact in artifacts:
            artifact.destination_path.parent.mkdir(parents=True, exist_ok=True)
            state = _PublicationState(
                artifact=artifact,
                backup_path=artifact.destination_path.with_name(
                    f".{artifact.destination_path.name}.{transaction_token}.backup"
                ),
                backup_moved=False,
                staged_moved=False,
            )
            states.append(state)
            if _path_exists(path=artifact.destination_path):
                os.replace(src=artifact.destination_path, dst=state.backup_path)
                state.backup_moved = True
            os.replace(src=artifact.staged_path, dst=artifact.destination_path)
            state.staged_moved = True
        if publish_hook is not None:
            publish_hook_started = True
            publish_hook()
    except BaseException as publication_error:
        rollback_errors: list[OSError] = []
        for state in reversed(states):
            try:
                if state.staged_moved:
                    _remove_path(path=state.artifact.destination_path)
                if state.backup_moved:
                    os.replace(
                        src=state.backup_path,
                        dst=state.artifact.destination_path,
                    )
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        for artifact in artifacts:
            _remove_path_best_effort(path=artifact.staged_path)
        rollback_hook_error: BaseException | None = None
        if publish_hook_started and rollback_hook is not None and not rollback_errors:
            try:
                rollback_hook()
            except BaseException as exc:
                rollback_hook_error = exc
        if rollback_errors:
            details = "; ".join(str(error) for error in rollback_errors)
            raise WidgetReconciliationError(
                f"publication failed and rollback was incomplete: {details}"
            ) from publication_error
        if rollback_hook_error is not None:
            raise WidgetReconciliationError(
                f"process-compose reload failed and restoring the prior project also failed: {rollback_hook_error}"
            ) from publication_error
        raise
    else:
        for state in states:
            _remove_path_best_effort(path=state.backup_path)


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_path(path: Path) -> None:
    if not _path_exists(path=path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _remove_path_best_effort(path: Path) -> None:
    try:
        _remove_path(path=path)
    except OSError:
        pass


def _registry_route_block(registry_path: Path) -> str:
    registry_root = _caddy_quote(value=str(registry_path.parent))
    registry_name = _caddy_quote(value=f"/{registry_path.name}")
    return (
        "# Generated by `widgets apply`; do not edit.\n"
        "@widgets_registry {\n"
        "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
        "\tpath /widgets/__admin/registry.json\n"
        "}\n"
        "handle @widgets_registry {\n"
        f"\troot * {registry_root}\n"
        f"\trewrite * {registry_name}\n"
        '\theader Cache-Control "no-store"\n'
        "\tfile_server\n"
        "}\n"
    )


def _widget_route_block(
    widget: widgets_core.WidgetManifest,
    port: int | None,
    static_root: Path,
    unavailable_path: Path,
) -> str:
    """Render one Widget as a single handle whose inner route fixes API-before-frontend order.

    The Caddyfile adapter re-sorts sibling top-level routes with a comparator that
    cannot rank multi-path matchers, so relative order of overlapping same-prefix
    routes is not preserved at the top level. Inside a `route` block the written
    order is authoritative, so each Widget's overlapping routes live in one.
    """
    matcher = f"widget_{widget.slug.replace('-', '_')}"
    base_path = f"/widgets/{widget.slug}"
    if widget.frontend.mode != "static" and port is None:
        return (
            f"@{matcher}_root {{\n"
            "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
            f"\tpath {base_path}\n"
            "}\n"
            f"redir @{matcher}_root {base_path}/ 308\n"
        )
    if port is not None:
        api_route = (
            f"\t\t@{matcher}_api path {base_path}/api {base_path}/api/*\n"
            f"\t\thandle @{matcher}_api {{\n"
            "\t\t\tvars humr_widget_route api\n"
            f"\t\t\turi strip_prefix {base_path}\n"
            f"\t\t\treverse_proxy 127.0.0.1:{port} {{\n"
            "\t\t\t\theader_up X-Forwarded-Host {header.X-Forwarded-Host}\n"
            f"\t\t\t\theader_up X-Forwarded-Prefix {base_path}\n"
            "\t\t\t}\n"
            "\t\t}\n"
        )
    else:
        api_route = ""
    if widget.frontend.mode == "static":
        frontend_route = _static_frontend_routes(widget=widget, static_root=static_root, base_path=base_path)
    else:
        frontend_route = _backend_frontend_route(matcher=matcher, port=port, base_path=base_path, unavailable_path=unavailable_path)
    return (
        f"@{matcher} {{\n"
        "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
        f"\tpath {base_path} {base_path}/*\n"
        "}\n"
        f"handle @{matcher} {{\n"
        "\troute {\n"
        f"\t\tredir {base_path} {base_path}/ 308\n"
        f"{api_route}"
        f"{frontend_route}"
        "\t}\n"
        "}\n"
    )


def _static_frontend_routes(widget: widgets_core.WidgetManifest, static_root: Path, base_path: str) -> str:
    entry = widget.frontend.entry
    if entry is None:
        raise ValueError(f"static Widget {widget.slug!r} has no frontend entry")
    entry_parts = PurePosixPath(entry).parts
    if not entry_parts or any(
        widgets_core.STATIC_ENTRY_COMPONENT_PATTERN.fullmatch(component) is None
        for component in entry_parts
    ):
        raise ValueError(f"static Widget {widget.slug!r} has an unsafe frontend entry")
    entry_name = entry_parts[-1]
    widget_static_root = _caddy_quote(value=str(static_root / widget.slug))
    entry_target = _caddy_quote(value=f"/{entry_name}")
    return (
        f"\t\thandle {base_path}/ {{\n"
        f"\t\t\troot * {widget_static_root}\n"
        f"\t\t\trewrite * {entry_target}\n"
        "\t\t\tfile_server\n"
        "\t\t}\n"
        "\t\thandle {\n"
        f"\t\t\turi strip_prefix {base_path}\n"
        f"\t\t\troot * {widget_static_root}\n"
        f"\t\t\ttry_files {{path}} {entry_target}\n"
        "\t\t\tfile_server\n"
        "\t\t}\n"
    )


def _backend_frontend_route(matcher: str, port: int, base_path: str, unavailable_path: Path) -> str:
    unavailable_root = _caddy_quote(value=str(unavailable_path.parent))
    unavailable_name = _caddy_quote(value=f"/{unavailable_path.name}")
    return (
        "\t\thandle {\n"
        "\t\t\tvars humr_widget_route frontend\n"
        f"\t\t\turi strip_prefix {base_path}\n"
        f"\t\t\treverse_proxy 127.0.0.1:{port} {{\n"
        "\t\t\t\theader_up X-Forwarded-Host {header.X-Forwarded-Host}\n"
        f"\t\t\t\theader_up X-Forwarded-Prefix {base_path}\n"
        f"\t\t\t\t@{matcher}_frontend_upstream_error status 5xx\n"
        f"\t\t\t\thandle_response @{matcher}_frontend_upstream_error {{\n"
        f"\t\t\t\t\troot * {unavailable_root}\n"
        f"\t\t\t\t\trewrite * {unavailable_name}\n"
        '\t\t\t\t\theader Cache-Control "no-store"\n'
        "\t\t\t\t\tfile_server {\n"
        "\t\t\t\t\t\tstatus 503\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t}\n"
        "\t\t\t}\n"
        "\t\t}\n"
    )


def _widget_backend_error_routes(unavailable_path: Path) -> str:
    unavailable_root = _caddy_quote(value=str(unavailable_path.parent))
    unavailable_name = _caddy_quote(value=f"/{unavailable_path.name}")
    return (
        "handle_errors 5xx {\n"
        "\t@widget_api_dial_error vars humr_widget_route api\n"
        "\thandle @widget_api_dial_error {\n"
        '\t\theader Cache-Control "no-store"\n'
        "\t\theader Content-Type application/json\n"
        '\t\trespond "{\\"error\\":\\"Widget backend unavailable\\"}" 503\n'
        "\t}\n"
        "\t@widget_frontend_dial_error vars humr_widget_route frontend\n"
        "\thandle @widget_frontend_dial_error {\n"
        f"\t\troot * {unavailable_root}\n"
        f"\t\trewrite * {unavailable_name}\n"
        '\t\theader Cache-Control "no-store"\n'
        "\t\tfile_server {\n"
        "\t\t\tstatus 503\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )


def _caddy_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
