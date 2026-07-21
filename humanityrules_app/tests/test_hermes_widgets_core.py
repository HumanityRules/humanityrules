"""Tests for the pure Hermes Widget manifest and registry contract."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _load_widgets_core_module() -> types.ModuleType:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    script_path = (
        repo_root
        / "template_repos"
        / "hermes_agent"
        / "humr_runtime"
        / "widgets"
        / "widgets_core.py"
    )
    spec = importlib.util.spec_from_file_location(
        name="widgets_core_under_test", location=str(script_path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Widgets core from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["widgets_core_under_test"] = module
    spec.loader.exec_module(module)
    return module


widgets_core = _load_widgets_core_module()


class TestHermesWidgetsCore(unittest.TestCase):
    def _write_widget(self, widgets_root: pathlib.Path, slug: str, manifest: object) -> pathlib.Path:
        widget_dir = widgets_root / slug
        widget_dir.mkdir(parents=True)
        (widget_dir / "widget.json").write_text(json.dumps(manifest), encoding="utf-8")
        return widget_dir

    def test_loads_static_widget_without_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            widget_dir = self._write_widget(
                widgets_root=widgets_root,
                slug="customer-dashboard",
                manifest={
                    "schema_version": 1,
                    "title": "Customer Dashboard",
                    "icon": "chart-bar",
                    "frontend": {"mode": "static", "entry": "public/index.html"},
                },
            )
            (widget_dir / "public").mkdir()
            (widget_dir / "public" / "index.html").write_text(
                "dashboard", encoding="utf-8"
            )

            manifest = widgets_core.load_widget(
                widgets_root=widgets_root, slug="customer-dashboard"
            )

        self.assertEqual(manifest.slug, "customer-dashboard")
        self.assertEqual(manifest.title, "Customer Dashboard")
        self.assertEqual(manifest.icon, "chart-bar")
        self.assertEqual(manifest.frontend.mode, "static")
        self.assertEqual(manifest.frontend.entry, "public/index.html")
        self.assertIsNone(manifest.backend)

    def test_loads_static_widget_with_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            widget_dir = self._write_widget(
                widgets_root=widgets_root,
                slug="customer-dashboard",
                manifest={
                    "schema_version": 1,
                    "title": "Customer Dashboard",
                    "frontend": {"mode": "static", "entry": "index.html"},
                    "backend": {"command": "uv run backend/server.py --token secret"},
                },
            )
            (widget_dir / "index.html").write_text("dashboard", encoding="utf-8")

            manifest = widgets_core.load_widget(
                widgets_root=widgets_root, slug="customer-dashboard"
            )

        self.assertEqual(manifest.frontend.mode, "static")
        self.assertEqual(
            manifest.backend.command, "uv run backend/server.py --token secret"
        )

    def test_loads_backend_served_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            self._write_widget(
                widgets_root=widgets_root,
                slug="server-dashboard",
                manifest={
                    "schema_version": 1,
                    "title": "Server Dashboard",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )

            manifest = widgets_core.load_widget(
                widgets_root=widgets_root, slug="server-dashboard"
            )

        self.assertEqual(manifest.frontend.mode, "backend")
        self.assertIsNone(manifest.frontend.entry)
        self.assertEqual(manifest.backend.command, "uv run server.py")

    def test_rejects_reserved_and_unsafe_slugs(self) -> None:
        for slug in ("__admin", "UPPER", "has space", "../escape", "ends-", "a"):
            with (
                self.subTest(slug=slug),
                self.assertRaises(widgets_core.WidgetValidationError),
            ):
                widgets_core.validate_slug(slug=slug)

    def test_rejects_path_traversal_and_symlink_escape(self) -> None:
        invalid_entries = (
            "../outside.html",
            "/etc/passwd",
            "public/../index.html",
            "public\\index.html",
        )
        for entry in invalid_entries:
            with (
                self.subTest(entry=entry),
                tempfile.TemporaryDirectory() as temporary_dir,
            ):
                root = pathlib.Path(temporary_dir)
                widgets_root = root / "widgets"
                widget_dir = self._write_widget(
                    widgets_root=widgets_root,
                    slug="dashboard",
                    manifest={
                        "schema_version": 1,
                        "title": "Dashboard",
                        "frontend": {"mode": "static", "entry": entry},
                    },
                )
                (widget_dir / "index.html").write_text("inside", encoding="utf-8")
                (root / "outside.html").write_text("outside", encoding="utf-8")

                with self.assertRaises(widgets_core.WidgetValidationError):
                    widgets_core.load_widget(
                        widgets_root=widgets_root, slug="dashboard"
                    )

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            widgets_root = root / "widgets"
            widget_dir = self._write_widget(
                widgets_root=widgets_root,
                slug="dashboard",
                manifest={
                    "schema_version": 1,
                    "title": "Dashboard",
                    "frontend": {"mode": "static", "entry": "index.html"},
                },
            )
            outside = root / "outside.html"
            outside.write_text("outside", encoding="utf-8")
            (widget_dir / "index.html").symlink_to(outside)

            with self.assertRaisesRegex(widgets_core.WidgetValidationError, "escapes"):
                widgets_core.load_widget(widgets_root=widgets_root, slug="dashboard")

    def test_rejects_widget_directory_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            widgets_root = root / "widgets"
            widgets_root.mkdir()
            outside_widget = root / "outside-widget"
            outside_widget.mkdir()
            (widgets_root / "dashboard").symlink_to(
                outside_widget, target_is_directory=True
            )

            with self.assertRaisesRegex(widgets_core.WidgetValidationError, "escapes"):
                widgets_core.load_widget(widgets_root=widgets_root, slug="dashboard")

    def test_strict_loader_translates_path_value_and_unicode_errors(self) -> None:
        path_errors = (
            ValueError("invalid path"),
            UnicodeEncodeError("utf-8", chr(0xD800), 0, 1, "surrogate path"),
        )
        for path_error in path_errors:
            with (
                self.subTest(error_type=type(path_error).__name__),
                tempfile.TemporaryDirectory() as temporary_dir,
                patch.object(widgets_core.Path, "resolve", side_effect=path_error),
                self.assertRaisesRegex(widgets_core.WidgetValidationError, "Widgets root.*unavailable"),
            ):
                widgets_core.load_widget(
                    widgets_root=pathlib.Path(temporary_dir),
                    slug="dashboard",
                )

    def test_rejects_malformed_and_contradictory_manifests(self) -> None:
        invalid_manifests = (
            [],
            {
                "schema_version": True,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
            {
                "schema_version": 2,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
            {
                "schema_version": 1,
                "title": " ",
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "icon": 7,
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "icon": None,
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
            {"schema_version": 1, "title": "Dashboard", "frontend": "static"},
            {"schema_version": 1, "title": "Dashboard", "frontend": {"mode": []}},
            {"schema_version": 1, "title": "Dashboard", "frontend": {"mode": "static"}},
            {
                "schema_version": 1,
                "title": "Dashboard",
                "frontend": {"mode": "backend", "entry": "index.html"},
                "backend": {"command": "run"},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
                "backend": None,
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
                "backend": {"command": " "},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "frontend": {"mode": "backend"},
                "backend": {"command": "run", "port": 4000},
            },
            {
                "schema_version": 1,
                "title": "Dashboard",
                "slug": "dashboard",
                "frontend": {"mode": "backend"},
                "backend": {"command": "run"},
            },
        )

        for index, manifest in enumerate(invalid_manifests):
            with (
                self.subTest(index=index),
                tempfile.TemporaryDirectory() as temporary_dir,
            ):
                widgets_root = pathlib.Path(temporary_dir)
                self._write_widget(
                    widgets_root=widgets_root, slug="dashboard", manifest=manifest
                )

                with self.assertRaises(widgets_core.WidgetValidationError):
                    widgets_core.load_widget(
                        widgets_root=widgets_root, slug="dashboard"
                    )

    def test_rejects_invalid_json_and_duplicate_keys(self) -> None:
        invalid_documents = (
            "{not json",
            '{"schema_version": 1, "schema_version": 1, "title": "Dashboard", "frontend": {"mode": "backend"}}',
        )
        for document in invalid_documents:
            with (
                self.subTest(document=document),
                tempfile.TemporaryDirectory() as temporary_dir,
            ):
                widgets_root = pathlib.Path(temporary_dir)
                widget_dir = widgets_root / "dashboard"
                widget_dir.mkdir(parents=True)
                (widget_dir / "widget.json").write_text(document, encoding="utf-8")

                with self.assertRaises(widgets_core.WidgetValidationError):
                    widgets_core.load_widget(
                        widgets_root=widgets_root, slug="dashboard"
                    )

    def test_discovery_reports_utf8_safe_failures_for_invalid_json_object_keys(self) -> None:
        invalid_keys = (("NUL", "bad\x00key"), ("surrogate", f"bad{chr(0xD800)}key"))
        for level in ("top-level", "nested"):
            for error_name, invalid_key in invalid_keys:
                with (
                    self.subTest(level=level, error_name=error_name),
                    tempfile.TemporaryDirectory() as temporary_dir,
                ):
                    widgets_root = pathlib.Path(temporary_dir)
                    invalid_manifest = {
                        "schema_version": 1,
                        "title": "Broken",
                        "frontend": {"mode": "backend"},
                        "backend": {"command": "uv run server.py"},
                    }
                    if level == "top-level":
                        invalid_manifest[invalid_key] = "invalid"
                    else:
                        invalid_manifest["frontend"][invalid_key] = "invalid"
                    self._write_widget(
                        widgets_root=widgets_root,
                        slug="broken-widget",
                        manifest=invalid_manifest,
                    )
                    self._write_widget(
                        widgets_root=widgets_root,
                        slug="valid-widget",
                        manifest={
                            "schema_version": 1,
                            "title": "Valid",
                            "frontend": {"mode": "backend"},
                            "backend": {"command": "uv run server.py"},
                        },
                    )

                    discovery = widgets_core.discover_widgets(widgets_root=widgets_root)

                    self.assertEqual([widget.slug for widget in discovery.widgets], ["valid-widget"])
                    self.assertEqual([failure.slug for failure in discovery.failures], ["broken-widget"])
                    self.assertIn("invalid JSON object key", discovery.failures[0].message)
                    self.assertIn(ascii(invalid_key), discovery.failures[0].message)
                    self.assertIn(error_name, discovery.failures[0].message)
                    discovery.failures[0].message.encode("utf-8")

    def test_rejects_nul_and_surrogate_code_points_in_all_manifest_strings(self) -> None:
        invalid_characters = (("NUL", "\x00"), ("surrogate", chr(0xD800)))
        locations = ("title", "icon", "frontend.entry", "backend.command")
        for error_name, invalid_character in invalid_characters:
            for location in locations:
                with (
                    self.subTest(error_name=error_name, location=location),
                    tempfile.TemporaryDirectory() as temporary_dir,
                ):
                    manifest = {
                        "schema_version": 1,
                        "title": "Dashboard",
                        "frontend": {"mode": "backend"},
                        "backend": {"command": "uv run server.py"},
                    }
                    if location == "title":
                        manifest["title"] = f"Dash{invalid_character}board"
                    elif location == "icon":
                        manifest["icon"] = f"chart{invalid_character}bar"
                    elif location == "frontend.entry":
                        manifest["frontend"] = {
                            "mode": "static",
                            "entry": f"index{invalid_character}.html",
                        }
                        del manifest["backend"]
                    else:
                        manifest["backend"] = {
                            "command": f"uv run{invalid_character}server.py"
                        }

                    widgets_root = pathlib.Path(temporary_dir)
                    self._write_widget(
                        widgets_root=widgets_root,
                        slug="dashboard",
                        manifest=manifest,
                    )

                    with self.assertRaisesRegex(widgets_core.WidgetValidationError, error_name):
                        widgets_core.load_widget(
                            widgets_root=widgets_root, slug="dashboard"
                        )

    def test_discovery_isolates_surrogate_static_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            self._write_widget(
                widgets_root=widgets_root,
                slug="broken-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Broken",
                    "frontend": {"mode": "static", "entry": f"index{chr(0xD800)}.html"},
                },
            )
            self._write_widget(
                widgets_root=widgets_root,
                slug="valid-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Valid",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )

            discovery = widgets_core.discover_widgets(widgets_root=widgets_root)

        self.assertEqual([widget.slug for widget in discovery.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in discovery.failures], ["broken-widget"])
        self.assertIn("frontend.entry", discovery.failures[0].message)
        self.assertIn("surrogate", discovery.failures[0].message)

    def test_discovery_isolates_oversized_json_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            broken_dir = widgets_root / "broken-widget"
            broken_dir.mkdir(parents=True)
            oversized_integer = "9" * 5000
            (broken_dir / "widget.json").write_text(
                '{"schema_version": '
                + oversized_integer
                + ', "title": "Broken", "frontend": {"mode": "backend"}}',
                encoding="utf-8",
            )
            self._write_widget(
                widgets_root=widgets_root,
                slug="valid-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Valid",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )

            discovery = widgets_core.discover_widgets(widgets_root=widgets_root)

        self.assertEqual([widget.slug for widget in discovery.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in discovery.failures], ["broken-widget"])
        self.assertIn("cannot read widget.json", discovery.failures[0].message)

    def test_discovery_keeps_valid_widgets_and_reports_failures_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            widgets_root = pathlib.Path(temporary_dir)
            alpha_dir = self._write_widget(
                widgets_root=widgets_root,
                slug="alpha-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Alpha",
                    "frontend": {"mode": "static", "entry": "index.html"},
                },
            )
            (alpha_dir / "index.html").write_text("alpha", encoding="utf-8")
            self._write_widget(
                widgets_root=widgets_root,
                slug="broken-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Broken",
                    "frontend": {"mode": "backend"},
                },
            )
            self._write_widget(
                widgets_root=widgets_root,
                slug="zulu-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Zulu",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run app.py"},
                },
            )

            discovery = widgets_core.discover_widgets(widgets_root=widgets_root)

        self.assertEqual(
            [widget.slug for widget in discovery.widgets],
            ["alpha-widget", "zulu-widget"],
        )
        self.assertEqual(
            [failure.slug for failure in discovery.failures], ["broken-widget"]
        )
        self.assertIn("backend is required", discovery.failures[0].message)

    def test_registry_is_sorted_and_exposes_only_host_fields(self) -> None:
        backend = widgets_core.WidgetBackend(
            command="uv run server.py --token highly-secret"
        )
        widgets = (
            widgets_core.WidgetManifest(
                schema_version=1,
                slug="zulu-widget",
                title="zulu",
                icon=None,
                frontend=widgets_core.WidgetFrontend(mode="backend", entry=None),
                backend=backend,
            ),
            widgets_core.WidgetManifest(
                schema_version=1,
                slug="bravo-widget",
                title="Alpha",
                icon="chart-bar",
                frontend=widgets_core.WidgetFrontend(mode="static", entry="index.html"),
                backend=None,
            ),
            widgets_core.WidgetManifest(
                schema_version=1,
                slug="alpha-widget",
                title="alpha",
                icon=None,
                frontend=widgets_core.WidgetFrontend(mode="backend", entry=None),
                backend=backend,
            ),
        )

        payload = widgets_core.build_registry_payload(widgets=widgets)
        serialized = json.dumps(payload)

        self.assertEqual(
            [entry["slug"] for entry in payload["widgets"]],
            ["alpha-widget", "bravo-widget", "zulu-widget"],
        )
        self.assertEqual(
            set(payload["widgets"][0]),
            {"slug", "title", "icon", "url"},
        )
        self.assertNotIn("highly-secret", serialized)
        self.assertNotIn("command", serialized)
        self.assertNotIn("entry", serialized)
        self.assertNotIn("port", serialized)

    def test_registry_write_is_safe_after_surrogate_title_and_icon_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            widgets_root = root / "widgets"
            invalid_character = chr(0xD800)
            self._write_widget(
                widgets_root=widgets_root,
                slug="bad-icon",
                manifest={
                    "schema_version": 1,
                    "title": "Bad Icon",
                    "icon": f"chart{invalid_character}bar",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )
            self._write_widget(
                widgets_root=widgets_root,
                slug="bad-title",
                manifest={
                    "schema_version": 1,
                    "title": f"Bad{invalid_character}Title",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )
            self._write_widget(
                widgets_root=widgets_root,
                slug="valid-widget",
                manifest={
                    "schema_version": 1,
                    "title": "Valid",
                    "frontend": {"mode": "backend"},
                    "backend": {"command": "uv run server.py"},
                },
            )
            registry_path = root / "config" / "registry.json"

            discovery = widgets_core.discover_widgets(widgets_root=widgets_root)
            payload = widgets_core.build_registry_payload(widgets=discovery.widgets)
            widgets_core.write_registry_json(registry_path=registry_path, payload=payload)

            persisted_payload = json.loads(registry_path.read_text(encoding="utf-8"))

        self.assertEqual([widget.slug for widget in discovery.widgets], ["valid-widget"])
        self.assertEqual(
            [failure.slug for failure in discovery.failures],
            ["bad-icon", "bad-title"],
        )
        self.assertTrue(all("surrogate" in failure.message for failure in discovery.failures))
        self.assertEqual(persisted_payload, payload)

    def test_registry_write_is_atomic_and_cleans_up_temporary_file(self) -> None:
        payload = widgets_core.build_registry_payload(widgets=())
        with tempfile.TemporaryDirectory() as temporary_dir:
            registry_path = pathlib.Path(temporary_dir) / "nested" / "registry.json"
            registry_path.parent.mkdir()
            registry_path.write_text("old registry", encoding="utf-8")
            real_replace = widgets_core.os.replace
            replacement_observations = []

            def observe_replace(*, src: pathlib.Path, dst: pathlib.Path) -> None:
                replacement_observations.append(
                    (src.parent, dst, registry_path.read_text(encoding="utf-8"))
                )
                real_replace(src=src, dst=dst)

            with patch.object(widgets_core.os, "replace", side_effect=observe_replace):
                widgets_core.write_registry_json(
                    registry_path=registry_path, payload=payload
                )

            self.assertEqual(
                json.loads(registry_path.read_text(encoding="utf-8")),
                payload,
            )
            self.assertEqual(
                replacement_observations,
                [(registry_path.parent, registry_path, "old registry")],
            )
            self.assertEqual(list(registry_path.parent.glob("*.tmp")), [])

    def test_widgets_lock_uses_exclusive_advisory_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            lock_path = pathlib.Path(temporary_dir) / "nested" / ".widgets.lock"
            with patch.object(widgets_core.fcntl, "flock") as flock:
                with widgets_core.WidgetsLock(lock_path=lock_path):
                    self.assertTrue(lock_path.is_file())

            self.assertEqual(flock.call_count, 2)
            self.assertEqual(
                flock.call_args_list[0].args[1], widgets_core.fcntl.LOCK_EX
            )
            self.assertEqual(
                flock.call_args_list[1].args[1], widgets_core.fcntl.LOCK_UN
            )
