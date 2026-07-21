"""Pure manifest, discovery, and registry primitives for HumR Widgets."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import IO, Literal, Self, TypedDict


WIDGETS_ROOT = Path("/workspace/widgets")
WIDGETS_CONFIG_ROOT = Path("/workspace/.config/widgets")
REGISTRY_PATH = WIDGETS_CONFIG_ROOT / "registry.json"
LOCK_PATH = WIDGETS_CONFIG_ROOT / ".widgets.lock"

MANIFEST_FILENAME = "widget.json"
MANIFEST_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")
RESERVED_SLUG_PREFIX = "__"


class RegistryWidget(TypedDict):
    slug: str
    title: str
    icon: str | None
    url: str


class RegistryPayload(TypedDict):
    schema_version: int
    widgets: list[RegistryWidget]


@dataclass(frozen=True)
class WidgetFrontend:
    mode: Literal["static", "backend"]
    entry: str | None


@dataclass(frozen=True)
class WidgetBackend:
    command: str


@dataclass(frozen=True)
class WidgetManifest:
    schema_version: Literal[1]
    slug: str
    title: str
    icon: str | None
    frontend: WidgetFrontend
    backend: WidgetBackend | None


@dataclass(frozen=True)
class WidgetValidationFailure:
    slug: str
    message: str


@dataclass(frozen=True)
class WidgetDiscovery:
    widgets: tuple[WidgetManifest, ...]
    failures: tuple[WidgetValidationFailure, ...]


class WidgetValidationError(ValueError):
    """Report an actionable validation failure for one Widget."""

    def __init__(self, slug: str, message: str) -> None:
        self.slug = slug
        self.message = message
        super().__init__(f"Widget {slug!r}: {message}")


class _DuplicateManifestKeyError(ValueError):
    pass


class _InvalidManifestKeyError(ValueError):
    pass


class WidgetsLock:
    """Serialize future Widget apply/reconcile mutations with an advisory lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._handle: IO[str] | None = None

    def __enter__(self) -> Self:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open(mode="a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        del exc_type, exc_value, traceback
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def validate_slug(slug: str) -> None:
    """Validate a user-owned Widget slug before using it in a filesystem path."""
    if slug.startswith(RESERVED_SLUG_PREFIX):
        raise WidgetValidationError(
            slug=slug, message="slugs beginning with '__' are reserved for the platform"
        )
    if SLUG_PATTERN.fullmatch(slug) is None:
        raise WidgetValidationError(
            slug=slug,
            message=(
                "slug must be 2-32 characters of lowercase letters, digits, or hyphens; "
                "it must start with a letter and end with a letter or digit"
            ),
        )


def load_widget(widgets_root: Path, slug: str) -> WidgetManifest:
    """Strictly load and validate one Widget from its authoritative slug directory."""
    validate_slug(slug=slug)
    try:
        resolved_root = widgets_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WidgetValidationError(
            slug=slug, message=f"Widgets root {widgets_root} is unavailable: {exc}"
        ) from exc
    if not resolved_root.is_dir():
        raise WidgetValidationError(
            slug=slug, message=f"Widgets root {widgets_root} is not a directory"
        )

    widget_dir = widgets_root / slug
    try:
        resolved_widget_dir = widget_dir.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WidgetValidationError(
            slug=slug, message=f"Widget directory {widget_dir} is unavailable: {exc}"
        ) from exc
    _require_contained_path(
        path=resolved_widget_dir,
        parent=resolved_root,
        slug=slug,
        description="Widget directory",
    )
    if not resolved_widget_dir.is_dir():
        raise WidgetValidationError(
            slug=slug, message=f"Widget path {widget_dir} is not a directory"
        )

    manifest_path = resolved_widget_dir / MANIFEST_FILENAME
    try:
        resolved_manifest_path = manifest_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WidgetValidationError(
            slug=slug, message=f"manifest {manifest_path} is unavailable: {exc}"
        ) from exc
    _require_contained_path(
        path=resolved_manifest_path,
        parent=resolved_widget_dir,
        slug=slug,
        description="manifest",
    )
    if not resolved_manifest_path.is_file():
        raise WidgetValidationError(
            slug=slug, message=f"manifest {manifest_path} is not a regular file"
        )

    payload = _read_manifest(manifest_path=resolved_manifest_path, slug=slug)
    return _parse_manifest(payload=payload, slug=slug, widget_dir=resolved_widget_dir)


def discover_widgets(widgets_root: Path) -> WidgetDiscovery:
    """Discover valid Widgets deterministically while retaining per-Widget failures."""
    try:
        candidates = sorted(widgets_root.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return WidgetDiscovery(widgets=(), failures=())
    except (OSError, RuntimeError, ValueError) as exc:
        failure = WidgetValidationFailure(
            slug=str(widgets_root), message=f"cannot scan Widgets root: {exc}"
        )
        return WidgetDiscovery(widgets=(), failures=(failure,))

    widgets: list[WidgetManifest] = []
    failures: list[WidgetValidationFailure] = []
    for candidate in candidates:
        slug = candidate.name
        try:
            widgets.append(load_widget(widgets_root=widgets_root, slug=slug))
        except WidgetValidationError as exc:
            failures.append(WidgetValidationFailure(slug=slug, message=exc.message))
    return WidgetDiscovery(widgets=tuple(widgets), failures=tuple(failures))


def build_registry_payload(widgets: Iterable[WidgetManifest]) -> RegistryPayload:
    """Build the public, host-consumed Widget registry in stable title order."""
    sorted_widgets = sorted(
        widgets, key=lambda widget: (widget.title.casefold(), widget.slug)
    )
    entries: list[RegistryWidget] = [
        {
            "slug": widget.slug,
            "title": widget.title,
            "icon": widget.icon,
            "url": f"/widgets/{widget.slug}/",
        }
        for widget in sorted_widgets
    ]
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "widgets": entries}


def write_registry_json(registry_path: Path, payload: RegistryPayload) -> None:
    """Atomically replace the registry with fsynced, deterministic JSON."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = registry_path.with_name(f".{registry_path.name}.{os.getpid()}.tmp")
    try:
        with temporary_path.open(mode="w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(src=temporary_path, dst=registry_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _require_contained_path(path: Path, parent: Path, slug: str, description: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise WidgetValidationError(
            slug=slug, message=f"{description} escapes {parent}"
        ) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        invalid_reason = _invalid_manifest_string_reason(value=key)
        if invalid_reason is not None:
            raise _InvalidManifestKeyError(f"invalid JSON object key {ascii(key)}: {invalid_reason}")
        if key in result:
            raise _DuplicateManifestKeyError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _read_manifest(manifest_path: Path, slug: str) -> dict[str, object]:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, RecursionError, ValueError) as exc:
        raise WidgetValidationError(
            slug=slug, message=f"cannot read {MANIFEST_FILENAME}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise WidgetValidationError(
            slug=slug, message=f"{MANIFEST_FILENAME} must contain a JSON object"
        )
    return payload


def _parse_manifest(payload: dict[str, object], slug: str, widget_dir: Path) -> WidgetManifest:
    _require_exact_keys(
        payload=payload,
        required={"schema_version", "title", "frontend"},
        optional={"icon", "backend"},
        slug=slug,
        location=MANIFEST_FILENAME,
    )

    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version != MANIFEST_SCHEMA_VERSION:
        raise WidgetValidationError(
            slug=slug,
            message=f"schema_version must be the integer {MANIFEST_SCHEMA_VERSION}",
        )

    title = _require_nonempty_string(
        value=payload["title"], slug=slug, location="title"
    )
    icon = None
    if "icon" in payload:
        icon = _require_nonempty_string(
            value=payload["icon"], slug=slug, location="icon"
        )

    frontend_payload = _require_object(
        value=payload["frontend"], slug=slug, location="frontend"
    )
    mode_value = frontend_payload.get("mode")
    if not isinstance(mode_value, str) or mode_value not in {"static", "backend"}:
        raise WidgetValidationError(
            slug=slug, message="frontend.mode must be 'static' or 'backend'"
        )
    if mode_value == "static":
        _require_exact_keys(
            payload=frontend_payload,
            required={"mode", "entry"},
            optional=set(),
            slug=slug,
            location="frontend",
        )
        entry = _validate_static_entry(
            value=frontend_payload["entry"], slug=slug, widget_dir=widget_dir
        )
        mode: Literal["static", "backend"] = "static"
    else:
        _require_exact_keys(
            payload=frontend_payload,
            required={"mode"},
            optional=set(),
            slug=slug,
            location="frontend",
        )
        entry = None
        mode = "backend"

    backend = None
    if "backend" in payload:
        backend = _parse_backend(value=payload["backend"], slug=slug)
    if mode == "backend" and backend is None:
        raise WidgetValidationError(
            slug=slug, message="backend is required when frontend.mode is 'backend'"
        )

    return WidgetManifest(
        schema_version=1,
        slug=slug,
        title=title,
        icon=icon,
        frontend=WidgetFrontend(mode=mode, entry=entry),
        backend=backend,
    )


def _require_exact_keys(payload: dict[str, object], required: set[str], optional: set[str], slug: str, location: str) -> None:
    missing = sorted(required - payload.keys())
    unexpected = sorted(payload.keys() - required - optional)
    if missing:
        raise WidgetValidationError(
            slug=slug,
            message=f"{location} is missing required keys: {', '.join(missing)}",
        )
    if unexpected:
        raise WidgetValidationError(
            slug=slug,
            message=f"{location} has unsupported keys: {', '.join(unexpected)}",
        )


def _require_nonempty_string(value: object, slug: str, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetValidationError(
            slug=slug, message=f"{location} must be a nonempty string"
        )
    invalid_reason = _invalid_manifest_string_reason(value=value)
    if invalid_reason is not None:
        raise WidgetValidationError(slug=slug, message=f"{location} {invalid_reason}")
    return value


def _invalid_manifest_string_reason(value: str) -> str | None:
    if "\x00" in value:
        return "must not contain NUL characters"
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return "must not contain Unicode surrogate code points"
    return None


def _require_object(value: object, slug: str, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WidgetValidationError(
            slug=slug, message=f"{location} must be a JSON object"
        )
    return value


def _validate_static_entry(value: object, slug: str, widget_dir: Path) -> str:
    entry = _require_nonempty_string(value=value, slug=slug, location="frontend.entry")
    if "\\" in entry:
        raise WidgetValidationError(
            slug=slug, message="frontend.entry must use a relative POSIX path"
        )
    raw_parts = entry.split("/")
    relative_path = PurePosixPath(entry)
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in raw_parts
    ):
        raise WidgetValidationError(
            slug=slug, message="frontend.entry must be a canonical relative path"
        )

    entry_path = widget_dir.joinpath(*relative_path.parts)
    try:
        resolved_entry_path = entry_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WidgetValidationError(
            slug=slug, message=f"static frontend entry {entry!r} is unavailable: {exc}"
        ) from exc
    _require_contained_path(
        path=resolved_entry_path,
        parent=widget_dir,
        slug=slug,
        description="static frontend entry",
    )
    if not resolved_entry_path.is_file():
        raise WidgetValidationError(
            slug=slug, message=f"static frontend entry {entry!r} is not a regular file"
        )
    return entry


def _parse_backend(value: object, slug: str) -> WidgetBackend:
    backend_payload = _require_object(value=value, slug=slug, location="backend")
    _require_exact_keys(
        payload=backend_payload,
        required={"command"},
        optional=set(),
        slug=slug,
        location="backend",
    )
    command = _require_nonempty_string(
        value=backend_payload["command"], slug=slug, location="backend.command"
    )
    return WidgetBackend(command=command)
