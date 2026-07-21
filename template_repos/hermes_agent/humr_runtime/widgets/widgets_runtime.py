"""Static routing and lifecycle reconciliation for HumR Widgets."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import widgets_core


WIDGETS_CADDY_PATH = Path("/workspace/.config/caddy/widgets.caddy")
WIDGETS_STATIC_ROOT = widgets_core.WIDGETS_CONFIG_ROOT / "static"
WIDGETS_TOMBSTONES_ROOT = widgets_core.WIDGETS_ROOT.parent / ".widgets-tombstones"

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


DEFAULT_PATHS = WidgetRuntimePaths(
    widgets_root=widgets_core.WIDGETS_ROOT,
    registry_path=widgets_core.REGISTRY_PATH,
    routes_path=WIDGETS_CADDY_PATH,
    static_root=WIDGETS_STATIC_ROOT,
    tombstones_root=WIDGETS_TOMBSTONES_ROOT,
    lock_path=widgets_core.LOCK_PATH,
)


def build_widgets_caddy_fragment(widgets: Iterable[widgets_core.WidgetManifest], static_root: Path, registry_path: Path) -> str:
    """Render deterministic same-origin registry and static Widget routes."""
    blocks = [_registry_route_block(registry_path=registry_path)]
    for widget in sorted(widgets, key=lambda item: item.slug):
        if widget.frontend.mode != "static":
            continue
        blocks.append(_static_widget_route_block(widget=widget, static_root=static_root))
    return "\n".join(blocks)


def reconcile_widgets(paths: WidgetRuntimePaths, target_slug: str | None) -> WidgetReconcileResult:
    """Rebuild registry, static snapshots, and routes from authoritative manifests."""
    paths.widgets_root.mkdir(parents=True, exist_ok=True)
    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        return _reconcile_widgets_unlocked(paths=paths, target_slug=target_slug)


def list_widgets(paths: WidgetRuntimePaths) -> widgets_core.WidgetDiscovery:
    """Read the authoritative manifests without consulting generated registry state."""
    paths.widgets_root.mkdir(parents=True, exist_ok=True)
    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        return widgets_core.discover_widgets(widgets_root=paths.widgets_root)


def delete_widget(paths: WidgetRuntimePaths, slug: str, confirmed: bool) -> WidgetReconcileResult:
    """Permanently remove exactly one Widget after reversible reconciliation."""
    if not confirmed:
        raise widgets_core.WidgetValidationError(
            slug=slug, message="delete requires --yes because deletion is permanent"
        )
    widgets_core.validate_slug(slug=slug)
    paths.widgets_root.mkdir(parents=True, exist_ok=True)

    with widgets_core.WidgetsLock(lock_path=paths.lock_path):
        widget_path = _validated_delete_path(widgets_root=paths.widgets_root, slug=slug)
        paths.tombstones_root.mkdir(parents=True, exist_ok=True)
        _require_tombstones_outside_discovery(
            widgets_root=paths.widgets_root,
            tombstones_root=paths.tombstones_root,
            slug=slug,
        )
        if widget_path.parent.stat().st_dev != paths.tombstones_root.stat().st_dev:
            raise widgets_core.WidgetValidationError(
                slug=slug, message="Widget source and deletion tombstone are not on the same filesystem"
            )
        tombstone_path = paths.tombstones_root / f"{slug}.{os.getpid()}.{secrets.token_hex(8)}"
        os.replace(src=widget_path, dst=tombstone_path)
        try:
            result = _reconcile_widgets_unlocked(paths=paths, target_slug=None)
        except BaseException:
            try:
                os.replace(src=tombstone_path, dst=widget_path)
            except OSError as restore_error:
                raise WidgetReconciliationError(
                    f"failed to restore Widget {slug!r} from {tombstone_path}: {restore_error}"
                ) from restore_error
            raise
        shutil.rmtree(tombstone_path)
        return result


def _reconcile_widgets_unlocked(paths: WidgetRuntimePaths, target_slug: str | None) -> WidgetReconcileResult:
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
        registry_payload = widgets_core.build_registry_payload(
            widgets=publishable_widgets
        )
        registry_document = _render_registry_json(payload=registry_payload)
        routes_document = build_widgets_caddy_fragment(
            widgets=publishable_widgets,
            static_root=paths.static_root,
            registry_path=paths.registry_path,
        )
        staged_registry = _stage_text(
            destination_path=paths.registry_path, document=registry_document
        )
        staged_routes = _stage_text(
            destination_path=paths.routes_path, document=routes_document
        )
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
            )
        )
    finally:
        for staged_path in (staged_snapshot, staged_registry, staged_routes):
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
        try:
            _copy_regular_file(
                source_fd=entry_fd,
                destination_path=destination_root / entry_parts[-1],
            )
        finally:
            os.close(entry_fd)

        _copy_assets_if_present(
            entry_parent_fd=entry_parent_fd,
            destination_root=destination_root,
            slug=widget.slug,
        )
    finally:
        for descriptor in reversed(opened_descriptors):
            os.close(descriptor)


def _copy_assets_if_present(entry_parent_fd: int, destination_root: Path, slug: str) -> None:
    try:
        asset_stat = os.stat("assets", dir_fd=entry_parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot inspect static assets: {exc}"
        ) from exc
    if stat.S_ISLNK(asset_stat.st_mode):
        raise widgets_core.WidgetValidationError(
            slug=slug, message="static assets directory must not be a symbolic link"
        )
    if not stat.S_ISDIR(asset_stat.st_mode):
        raise widgets_core.WidgetValidationError(
            slug=slug, message="static assets must be a directory"
        )

    assets_fd = _open_directory_at(
        parent_fd=entry_parent_fd,
        name="assets",
        slug=slug,
        description="static assets directory",
    )
    try:
        destination_assets = destination_root / "assets"
        destination_assets.mkdir()
        _copy_asset_directory(
            source_fd=assets_fd,
            destination_dir=destination_assets,
            slug=slug,
            relative_dir=PurePosixPath("assets"),
        )
    finally:
        os.close(assets_fd)


def _copy_asset_directory(source_fd: int, destination_dir: Path, slug: str, relative_dir: PurePosixPath) -> None:
    try:
        names = sorted(os.listdir(source_fd))
    except OSError as exc:
        raise widgets_core.WidgetValidationError(
            slug=slug, message=f"cannot list static asset directory {relative_dir}: {exc}"
        ) from exc
    for name in names:
        relative_path = relative_dir / name
        if name.startswith("."):
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"static assets must not contain dotfile {relative_path}"
            )
        try:
            source_stat = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"cannot inspect static asset {relative_path}: {exc}"
            ) from exc
        if stat.S_ISLNK(source_stat.st_mode):
            raise widgets_core.WidgetValidationError(
                slug=slug, message=f"static asset {relative_path} must not be a symbolic link"
            )
        if stat.S_ISDIR(source_stat.st_mode):
            destination_child = destination_dir / name
            destination_child.mkdir()
            child_fd = _open_directory_at(
                parent_fd=source_fd,
                name=name,
                slug=slug,
                description=f"static asset directory {relative_path}",
            )
            try:
                _copy_asset_directory(
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
                slug=slug, message=f"static asset {relative_path} must be a regular file or directory"
            )
        source_file_fd = _open_regular_file_at(
            parent_fd=source_fd,
            name=name,
            slug=slug,
            description=f"static asset {relative_path}",
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


def _publish_artifacts(artifacts: tuple[_PublicationArtifact, ...]) -> None:
    transaction_token = secrets.token_hex(8)
    states: list[_PublicationState] = []
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
            _remove_path(path=artifact.staged_path)
        if rollback_errors:
            details = "; ".join(str(error) for error in rollback_errors)
            raise WidgetReconciliationError(
                f"publication failed and rollback was incomplete: {details}"
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


def _static_widget_route_block(widget: widgets_core.WidgetManifest, static_root: Path) -> str:
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
    matcher = f"widget_{widget.slug.replace('-', '_')}"
    base_path = f"/widgets/{widget.slug}"
    safe_assets_pattern = f"^{base_path}/assets/(?:[^./][^/]*/)*[^./][^/]*$"
    return (
        f"@{matcher}_root {{\n"
        "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
        f"\tpath {base_path}\n"
        "}\n"
        f"redir @{matcher}_root {base_path}/ 308\n"
        f"@{matcher}_assets {{\n"
        "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
        f"\tpath_regexp {matcher}_assets_path {safe_assets_pattern}\n"
        "}\n"
        f"handle @{matcher}_assets {{\n"
        f"\turi strip_prefix {base_path}\n"
        f"\troot * {widget_static_root}\n"
        "\tfile_server\n"
        "}\n"
        f"@{matcher}_frontend {{\n"
        "\theader X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}\n"
        f"\tpath {base_path}/*\n"
        "}\n"
        f"handle @{matcher}_frontend {{\n"
        f"\troot * {widget_static_root}\n"
        f"\trewrite * {entry_target}\n"
        "\tfile_server\n"
        "}\n"
    )


def _caddy_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
