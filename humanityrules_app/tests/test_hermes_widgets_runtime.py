"""Tests for the Hermes static Widget runtime and CLI contract."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _runtime_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "template_repos" / "hermes_agent" / "humr_runtime"


def _load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    loader = None
    if path.suffix == "":
        loader = importlib.machinery.SourceFileLoader(fullname=name, path=str(path))
    spec = importlib.util.spec_from_file_location(
        name=name, location=str(path), loader=loader
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


widgets_dir = _runtime_dir() / "widgets"
webapps_dir = _runtime_dir() / "webapps"
for runtime_path in (str(widgets_dir), str(webapps_dir)):
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)

widgets_core = _load_module(name="widgets_core", path=widgets_dir / "widgets_core.py")
widgets_runtime = _load_module(
    name="widgets_runtime", path=widgets_dir / "widgets_runtime.py"
)
widgets_cli = _load_module(name="widgets_cli_under_test", path=widgets_dir / "widgets")
webapps_lib = _load_module(name="webapps_lib_for_widgets_test", path=webapps_dir / "webapps_lib.py")


class TestHermesWidgetsRuntime(unittest.TestCase):
    def _paths(self, root: pathlib.Path) -> object:
        return widgets_runtime.WidgetRuntimePaths(
            widgets_root=root / "widgets",
            registry_path=root / "config" / "widgets" / "registry.json",
            routes_path=root / "config" / "caddy" / "widgets.caddy",
            static_root=root / "config" / "widgets" / "static",
            tombstones_root=root / ".widgets-tombstones",
            lock_path=root / "config" / "widgets" / ".widgets.lock",
        )

    def _write_static_widget(
        self,
        paths: object,
        slug: str,
        title: str,
        entry: str,
        backend: bool,
    ) -> pathlib.Path:
        widget_dir = paths.widgets_root / slug
        entry_path = widget_dir.joinpath(*pathlib.PurePosixPath(entry).parts)
        entry_path.parent.mkdir(parents=True)
        entry_path.write_text(f"<h1>{title}</h1>", encoding="utf-8")
        manifest: dict[str, object] = {
            "schema_version": 1,
            "title": title,
            "frontend": {"mode": "static", "entry": entry},
        }
        if backend:
            manifest["backend"] = {"command": "uv run backend/server.py"}
        (widget_dir / "widget.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return widget_dir

    def _write_invalid_widget(self, paths: object, slug: str) -> None:
        widget_dir = paths.widgets_root / slug
        widget_dir.mkdir(parents=True)
        (widget_dir / "widget.json").write_text("{broken", encoding="utf-8")

    def _snapshot_files(self, static_root: pathlib.Path) -> dict[str, bytes]:
        return {
            path.relative_to(static_root).as_posix(): path.read_bytes()
            for path in sorted(static_root.rglob("*"))
            if path.is_file()
        }

    def test_static_routes_expose_only_entry_and_safe_sibling_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_dir = self._write_static_widget(
                paths=paths,
                slug="customer-dashboard",
                title="Customer Dashboard",
                entry="dist/site/app.html",
                backend=True,
            )
            manifest = widgets_core.load_widget(
                widgets_root=paths.widgets_root, slug="customer-dashboard"
            )

            fragment = widgets_runtime.build_widgets_caddy_fragment(
                widgets=(manifest,),
                static_root=paths.static_root,
                registry_path=paths.registry_path,
            )

        self.assertIn(f'root * "{paths.static_root / "customer-dashboard"}"', fragment)
        self.assertIn('rewrite * "/app.html"', fragment)
        self.assertIn("path /widgets/customer-dashboard", fragment)
        self.assertIn("redir @widget_customer_dashboard_root /widgets/customer-dashboard/ 308", fragment)
        self.assertIn("path_regexp widget_customer_dashboard_assets_path", fragment)
        self.assertIn("(?:[^./][^/]*/)*[^./][^/]*", fragment)
        self.assertNotIn(str(widget_dir), fragment)
        self.assertNotIn("dist/site", fragment)
        self.assertNotIn("widget.json", fragment)
        self.assertNotIn("backend/server.py", fragment)

        malicious = widgets_core.WidgetManifest(
            schema_version=1,
            slug="customer-dashboard",
            title="Customer Dashboard",
            icon=None,
            frontend=widgets_core.WidgetFrontend(
                mode="static", entry="{http.request.header.X-Root}"
            ),
            backend=None,
        )
        with self.assertRaisesRegex(ValueError, "unsafe frontend entry"):
            widgets_runtime.build_widgets_caddy_fragment(
                widgets=(malicious,),
                static_root=paths.static_root,
                registry_path=paths.registry_path,
            )

    def test_manifest_rejects_caddy_interpolation_and_unsafe_entry_components(self) -> None:
        invalid_entries = (
            ".index.html",
            "dist/.private/index.html",
            "{http.request.header.X-Root}",
            "dist/$HUMR_PUBLIC_HOSTNAME.html",
            "dist/has space.html",
            "dist/semi;colon.html",
            "dist/café.html",
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as temporary_dir:
                paths = self._paths(root=pathlib.Path(temporary_dir))
                self._write_static_widget(
                    paths=paths,
                    slug="dashboard",
                    title="Dashboard",
                    entry=entry,
                    backend=False,
                )

                with self.assertRaisesRegex(
                    widgets_core.WidgetValidationError, "components"
                ):
                    widgets_core.load_widget(
                        widgets_root=paths.widgets_root, slug="dashboard"
                    )

    def test_reconcile_writes_registry_and_deterministic_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            zulu_dir = self._write_static_widget(
                paths=paths,
                slug="zulu-widget",
                title="Alpha title",
                entry="index.html",
                backend=False,
            )
            alpha_dir = self._write_static_widget(
                paths=paths,
                slug="alpha-widget",
                title="Zulu title",
                entry="public/index.html",
                backend=False,
            )
            (zulu_dir / "assets").mkdir()
            (zulu_dir / "assets" / "app.js").write_text("zulu js", encoding="utf-8")
            (alpha_dir / "public" / "assets" / "nested").mkdir(parents=True)
            (alpha_dir / "public" / "assets" / "nested" / "app.css").write_text(
                "alpha css", encoding="utf-8"
            )
            (alpha_dir / "backend.py").write_text("secret", encoding="utf-8")

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)
            registry = json.loads(paths.registry_path.read_text(encoding="utf-8"))
            routes = paths.routes_path.read_text(encoding="utf-8")
            snapshot = self._snapshot_files(static_root=paths.static_root)
            live_asset = alpha_dir / "public" / "assets" / "nested" / "app.css"
            outside = pathlib.Path(temporary_dir) / "outside.css"
            outside.write_text("outside secret", encoding="utf-8")
            live_asset.unlink()
            live_asset.symlink_to(outside)
            snapshot_after_source_change = self._snapshot_files(
                static_root=paths.static_root
            )

        self.assertEqual([widget.slug for widget in result.widgets], ["alpha-widget", "zulu-widget"])
        self.assertEqual(
            [widget["slug"] for widget in registry["widgets"]],
            ["zulu-widget", "alpha-widget"],
        )
        self.assertIn("path /widgets/__admin/registry.json", routes)
        self.assertIn('header Cache-Control "no-store"', routes)
        self.assertLess(routes.index("widget_alpha_widget_root"), routes.index("widget_zulu_widget_root"))
        self.assertEqual(
            snapshot,
            {
                "alpha-widget/assets/nested/app.css": b"alpha css",
                "alpha-widget/index.html": b"<h1>Zulu title</h1>",
                "zulu-widget/assets/app.js": b"zulu js",
                "zulu-widget/index.html": b"<h1>Alpha title</h1>",
            },
        )
        self.assertNotIn("backend.py", routes)
        self.assertEqual(snapshot_after_source_change, snapshot)

    def test_target_apply_fails_without_changing_last_good_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="valid-widget",
                title="Valid",
                entry="index.html",
                backend=False,
            )
            widgets_runtime.reconcile_widgets(paths=paths, target_slug="valid-widget")
            old_registry = paths.registry_path.read_text(encoding="utf-8")
            old_routes = paths.routes_path.read_text(encoding="utf-8")
            self._write_invalid_widget(paths=paths, slug="broken-widget")

            with self.assertRaises(widgets_core.WidgetValidationError):
                widgets_runtime.reconcile_widgets(
                    paths=paths, target_slug="broken-widget"
                )

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), old_registry)
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), old_routes)

            with self.assertRaises(widgets_core.WidgetValidationError):
                widgets_runtime.reconcile_widgets(
                    paths=paths, target_slug="missing-widget"
                )

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), old_registry)
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), old_routes)

    def test_target_apply_skips_unrelated_invalid_widget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="valid-widget",
                title="Valid",
                entry="index.html",
                backend=False,
            )
            self._write_invalid_widget(paths=paths, slug="broken-widget")

            result = widgets_runtime.reconcile_widgets(
                paths=paths, target_slug="valid-widget"
            )

        self.assertEqual([widget.slug for widget in result.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in result.failures], ["broken-widget"])

    def test_asset_symlink_is_rejected_and_never_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            paths = self._paths(root=root)
            self._write_static_widget(
                paths=paths,
                slug="valid-widget",
                title="Valid",
                entry="index.html",
                backend=False,
            )
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)

            linked_dir = self._write_static_widget(
                paths=paths,
                slug="linked-widget",
                title="Linked",
                entry="public/index.html",
                backend=False,
            )
            outside = root / "outside-secret.txt"
            outside.write_text("must never be public", encoding="utf-8")
            assets_dir = linked_dir / "public" / "assets"
            assets_dir.mkdir()
            (assets_dir / "secret.txt").symlink_to(outside)

            with self.assertRaisesRegex(
                widgets_core.WidgetValidationError, "symbolic link"
            ):
                widgets_runtime.reconcile_widgets(
                    paths=paths, target_slug="linked-widget"
                )

            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)
            registry = paths.registry_path.read_text(encoding="utf-8")
            routes = paths.routes_path.read_text(encoding="utf-8")
            snapshot = self._snapshot_files(static_root=paths.static_root)

        self.assertEqual([widget.slug for widget in result.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in result.failures], ["linked-widget"])
        self.assertNotIn("linked-widget", registry)
        self.assertNotIn("linked-widget", routes)
        self.assertNotIn(b"must never be public", snapshot.values())

    def test_special_asset_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_dir = self._write_static_widget(
                paths=paths,
                slug="special-widget",
                title="Special",
                entry="index.html",
                backend=False,
            )
            assets_dir = widget_dir / "assets"
            assets_dir.mkdir()
            os.mkfifo(assets_dir / "pipe")

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)

        self.assertEqual(result.widgets, ())
        self.assertEqual([failure.slug for failure in result.failures], ["special-widget"])
        self.assertIn("regular file or directory", result.failures[0].message)

    def test_apply_all_succeeds_and_reports_invalid_widgets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="valid-widget",
                title="Valid",
                entry="index.html",
                backend=False,
            )
            self._write_invalid_widget(paths=paths, slug="broken-widget")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = widgets_cli.main(argv=["apply", "--all"], paths=paths)

        self.assertEqual(exit_code, 0)
        self.assertIn("reconciled 1 Widget(s)", stdout.getvalue())
        self.assertIn("skipping 'broken-widget'", stderr.getvalue())

    def test_generation_failure_preserves_both_last_good_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="valid-widget",
                title="Valid",
                entry="index.html",
                backend=False,
            )
            paths.registry_path.parent.mkdir(parents=True)
            paths.routes_path.parent.mkdir(parents=True)
            paths.registry_path.write_text("old registry\n", encoding="utf-8")
            paths.routes_path.write_text("old routes\n", encoding="utf-8")

            with (
                patch.object(
                    widgets_runtime,
                    "build_widgets_caddy_fragment",
                    side_effect=RuntimeError("generation failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "generation failed"),
            ):
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), "old registry\n")
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), "old routes\n")

    def test_later_publication_failure_rolls_back_snapshot_registry_and_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            paths = self._paths(root=root)
            self._write_static_widget(
                paths=paths,
                slug="first-widget",
                title="First",
                entry="index.html",
                backend=False,
            )
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)
            self._write_static_widget(
                paths=paths,
                slug="second-widget",
                title="Second",
                entry="public/app.html",
                backend=False,
            )
            real_replace = widgets_runtime.os.replace
            replace_calls = 0

            def fail_sixth_replace(*, src: pathlib.Path, dst: pathlib.Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 6:
                    raise OSError("injected route publication failure")
                real_replace(src=src, dst=dst)

            with (
                patch.object(
                    widgets_runtime.os,
                    "replace",
                    side_effect=fail_sixth_replace,
                ),
                self.assertRaisesRegex(OSError, "injected route publication failure"),
            ):
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)

            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )
            self.assertEqual(list(root.rglob("*.backup")), [])
            self.assertEqual(list(root.rglob("*.stage")), [])

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)

        self.assertEqual(
            [widget.slug for widget in result.widgets],
            ["first-widget", "second-widget"],
        )

    def test_root_discovery_failure_preserves_last_good_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            paths.widgets_root.mkdir(parents=True)
            paths.registry_path.parent.mkdir(parents=True)
            paths.routes_path.parent.mkdir(parents=True)
            paths.registry_path.write_text("old registry\n", encoding="utf-8")
            paths.routes_path.write_text("old routes\n", encoding="utf-8")
            root_failure = widgets_core.WidgetDiscovery(
                widgets=(),
                failures=(
                    widgets_core.WidgetValidationFailure(
                        slug=str(paths.widgets_root), message="cannot scan Widgets root"
                    ),
                ),
            )

            with (
                patch.object(
                    widgets_runtime.widgets_core,
                    "discover_widgets",
                    return_value=root_failure,
                ),
                self.assertRaisesRegex(
                    widgets_runtime.WidgetReconciliationError,
                    "cannot scan Widgets root",
                ),
            ):
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), "old registry\n")
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), "old routes\n")

    def test_list_is_manifest_derived_and_delete_is_exact_and_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            deleted_path = self._write_static_widget(
                paths=paths,
                slug="delete-me",
                title="Delete Me",
                entry="index.html",
                backend=False,
            )
            preserved_path = self._write_static_widget(
                paths=paths,
                slug="delete-me-too",
                title="Keep Me",
                entry="index.html",
                backend=False,
            )
            paths.registry_path.parent.mkdir(parents=True)
            paths.registry_path.write_text(
                '{"schema_version": 1, "widgets": []}\n', encoding="utf-8"
            )

            discovery = widgets_runtime.list_widgets(paths=paths)
            with self.assertRaisesRegex(widgets_core.WidgetValidationError, "--yes"):
                widgets_runtime.delete_widget(
                    paths=paths, slug="delete-me", confirmed=False
                )
            result = widgets_runtime.delete_widget(
                paths=paths, slug="delete-me", confirmed=True
            )

            self.assertFalse(deleted_path.exists())
            self.assertTrue(preserved_path.exists())
            self.assertEqual(list(paths.tombstones_root.iterdir()), [])
            persisted_registry = json.loads(
                paths.registry_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                [entry["slug"] for entry in persisted_registry["widgets"]],
                ["delete-me-too"],
            )
            self.assertFalse((paths.static_root / "delete-me").exists())

        self.assertEqual(
            [widget.slug for widget in discovery.widgets],
            ["delete-me", "delete-me-too"],
        )
        self.assertEqual([widget.slug for widget in result.widgets], ["delete-me-too"])

    def test_delete_restores_source_and_derived_state_when_publication_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_path = self._write_static_widget(
                paths=paths,
                slug="restore-me",
                title="Restore Me",
                entry="index.html",
                backend=False,
            )
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None)
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)
            discovered_during_delete: list[str] = []

            def fail_publication(*, artifacts: object) -> None:
                del artifacts
                discovered_during_delete.extend(
                    path.name for path in paths.widgets_root.iterdir()
                )
                raise OSError("injected delete publication failure")

            with (
                patch.object(
                    widgets_runtime,
                    "_publish_artifacts",
                    side_effect=fail_publication,
                ),
                self.assertRaisesRegex(OSError, "injected delete publication failure"),
            ):
                widgets_runtime.delete_widget(
                    paths=paths, slug="restore-me", confirmed=True
                )

            self.assertTrue(widget_path.is_dir())
            self.assertTrue((widget_path / "widget.json").is_file())
            self.assertEqual(discovered_during_delete, [])
            self.assertEqual(list(paths.tombstones_root.iterdir()), [])
            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )

    def test_delete_refuses_reserved_and_symbolic_link_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            paths = self._paths(root=root)
            paths.widgets_root.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (paths.widgets_root / "linked-widget").symlink_to(
                outside, target_is_directory=True
            )

            with self.assertRaises(widgets_core.WidgetValidationError):
                widgets_runtime.delete_widget(
                    paths=paths, slug="__admin", confirmed=True
                )
            with self.assertRaisesRegex(
                widgets_core.WidgetValidationError, "symbolic-link"
            ):
                widgets_runtime.delete_widget(
                    paths=paths, slug="linked-widget", confirmed=True
                )

            self.assertTrue(outside.exists())

    def test_webapps_and_widgets_own_separate_imported_fragments(self) -> None:
        caddyfile = (webapps_dir / "Caddyfile").read_text(encoding="utf-8")

        self.assertEqual(
            webapps_lib.WEBAPPS_PROJECT.route_file,
            pathlib.Path("/workspace/.config/caddy/webapps.caddy"),
        )
        self.assertIn("import /workspace/.config/caddy/webapps.caddy", caddyfile)
        self.assertIn("import /workspace/.config/caddy/widgets.caddy", caddyfile)

    def test_webapps_route_regeneration_does_not_overwrite_widgets_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            webapps_fragment = root / "webapps.caddy"
            widgets_fragment = root / "widgets.caddy"
            widgets_fragment.write_text("Widget routes stay intact\n", encoding="utf-8")
            project = webapps_lib.ProcessComposeProject(
                config_dir=root / "process-compose",
                port="9957",
                lock_name=".webapps.lock",
                required_dirs=(),
                route_file=webapps_fragment,
            )
            document = {
                "processes": {
                    "dashboard": {
                        "environment": ["WEBAPP_PORT=4000"],
                    }
                }
            }

            with (
                patch.object(webapps_lib, "WEBAPPS_PROJECT", project),
                patch.dict(
                    webapps_lib.os.environ,
                    {"HUMR_PUBLIC_HOSTNAME": "agent.example.com"},
                    clear=True,
                ),
            ):
                webapps_lib.regenerate_routes(doc=document)

            generated_webapps = webapps_fragment.read_text(encoding="utf-8")
            preserved_widgets = widgets_fragment.read_text(encoding="utf-8")

        self.assertIn("dashboard-agent.example.com", generated_webapps)
        self.assertEqual(preserved_widgets, "Widget routes stay intact\n")

    def test_boot_docker_and_profile_wire_widgets_before_supervisors(self) -> None:
        hermes_agent = _runtime_dir().parent
        webui_script = (_runtime_dir() / "webui.sh").read_text(encoding="utf-8")
        dockerfile = (hermes_agent / "Dockerfile").read_text(encoding="utf-8")
        profile = json.loads(
            (_runtime_dir() / "hermes-nono-profile.json").read_text(encoding="utf-8")
        )

        self.assertIn("widgets apply --all", webui_script)
        self.assertLess(webui_script.index("reconcile_widgets\n"), webui_script.index("start_system_process_compose\n"))
        self.assertIn("/workspace/.config/caddy/webapps.caddy", webui_script)
        self.assertIn("/workspace/.config/caddy/widgets.caddy", webui_script)
        self.assertIn("ln -sf /opt/humr/runtime/widgets/widgets /opt/humr/bin/widgets", dockerfile)
        self.assertTrue((widgets_dir / "widgets").stat().st_mode & 0o111)
        self.assertIn("/opt/humr/runtime/widgets/widgets", profile["filesystem"]["read_file"])
        self.assertIn("/opt/humr/runtime/widgets/widgets_core.py", profile["filesystem"]["read_file"])
        self.assertIn("/opt/humr/runtime/widgets/widgets_runtime.py", profile["filesystem"]["read_file"])
        self.assertIn("/opt/humr/bin/widgets", profile["filesystem"]["read_file"])


if __name__ == "__main__":
    unittest.main()
