"""Tests for the Hermes Widget runtime and CLI contract."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import call, patch


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
process_supervisor_dir = _runtime_dir() / "process_supervisor"
for runtime_path in (
    str(widgets_dir),
    str(webapps_dir),
    str(process_supervisor_dir),
):
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)

process_supervisor = _load_module(
    name="process_supervisor",
    path=process_supervisor_dir / "process_supervisor.py",
)
widgets_core = _load_module(name="widgets_core", path=widgets_dir / "widgets_core.py")
widgets_runtime = _load_module(
    name="widgets_runtime", path=widgets_dir / "widgets_runtime.py"
)
widgets_cli = _load_module(name="widgets_cli_under_test", path=widgets_dir / "widgets")
webapps_lib = _load_module(name="webapps_lib_for_widgets_test", path=webapps_dir / "webapps_lib.py")
webapps_cli = _load_module(name="webapps_cli_for_widgets_test", path=webapps_dir / "webapps")


class TestHermesWidgetsRuntime(unittest.TestCase):
    def _paths(self, root: pathlib.Path) -> object:
        process_project = process_supervisor.ProcessComposeProject(
            config_dir=root / "config" / "process-compose" / "app-workloads",
            port="9957",
            lock_name=".app-workloads.lock",
            required_dirs=(root / "config" / "caddy",),
        )
        return widgets_runtime.WidgetRuntimePaths(
            widgets_root=root / "widgets",
            registry_path=root / "config" / "widgets" / "registry.json",
            routes_path=root / "config" / "caddy" / "widgets.caddy",
            static_root=root / "config" / "widgets" / "static",
            tombstones_root=root / ".widgets-tombstones",
            logs_root=root / "config" / "widgets" / "logs",
            unavailable_path=widgets_dir / "widget-unavailable.html",
            process_project=process_project,
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

    def _write_backend_widget(
        self, paths: object, slug: str, title: str, command: str
    ) -> pathlib.Path:
        widget_dir = paths.widgets_root / slug
        widget_dir.mkdir(parents=True)
        (widget_dir / "widget.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "title": title,
                    "frontend": {"mode": "backend"},
                    "backend": {"command": command},
                }
            ),
            encoding="utf-8",
        )
        return widget_dir

    def _snapshot_files(self, static_root: pathlib.Path) -> dict[str, bytes]:
        return {
            path.relative_to(static_root).as_posix(): path.read_bytes()
            for path in sorted(static_root.rglob("*"))
            if path.is_file()
        }

    def test_static_routes_serve_existing_files_and_fall_back_to_entry(self) -> None:
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
                backend_ports={},
                unavailable_path=paths.unavailable_path,
            )

        self.assertIn(f'root * "{paths.static_root / "customer-dashboard"}"', fragment)
        self.assertIn('rewrite * "/app.html"', fragment)
        self.assertIn("path /widgets/customer-dashboard", fragment)
        self.assertIn("redir @widget_customer_dashboard_root /widgets/customer-dashboard/ 308", fragment)
        self.assertIn("path /widgets/customer-dashboard/", fragment)
        self.assertIn("uri strip_prefix /widgets/customer-dashboard", fragment)
        self.assertIn('try_files {path} "/app.html"', fragment)
        self.assertNotIn("path_regexp widget_customer_dashboard_assets_path", fragment)
        self.assertNotIn(str(widget_dir), fragment)
        self.assertNotIn("dist/site", fragment)

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
                backend_ports={},
                unavailable_path=paths.unavailable_path,
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
            (zulu_dir / "contacts.json").write_text('{"contacts": []}', encoding="utf-8")
            (alpha_dir / "public" / "styles").mkdir(parents=True)
            (alpha_dir / "public" / "styles" / "app.css").write_text(
                "alpha css", encoding="utf-8"
            )
            (alpha_dir / "public" / "data").mkdir()
            (alpha_dir / "public" / "data" / "contacts.json").write_text(
                '{"contacts": []}', encoding="utf-8"
            )
            (alpha_dir / "public" / "app.js").write_text("alpha js", encoding="utf-8")
            (alpha_dir / "backend.py").write_text("secret", encoding="utf-8")

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)
            registry = json.loads(paths.registry_path.read_text(encoding="utf-8"))
            routes = paths.routes_path.read_text(encoding="utf-8")
            snapshot = self._snapshot_files(static_root=paths.static_root)
            live_asset = alpha_dir / "public" / "styles" / "app.css"
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
                "alpha-widget/app.js": b"alpha js",
                "alpha-widget/data/contacts.json": b'{"contacts": []}',
                "alpha-widget/index.html": b"<h1>Zulu title</h1>",
                "alpha-widget/styles/app.css": b"alpha css",
                "zulu-widget/assets/app.js": b"zulu js",
                "zulu-widget/contacts.json": b'{"contacts": []}',
                "zulu-widget/index.html": b"<h1>Alpha title</h1>",
                "zulu-widget/widget.json": (
                    b'{"schema_version": 1, "title": "Alpha title", '
                    b'"frontend": {"mode": "static", "entry": "index.html"}}'
                ),
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
            widgets_runtime.reconcile_widgets(paths=paths, target_slug="valid-widget", update_processes=False)
            old_registry = paths.registry_path.read_text(encoding="utf-8")
            old_routes = paths.routes_path.read_text(encoding="utf-8")
            self._write_invalid_widget(paths=paths, slug="broken-widget")

            with self.assertRaises(widgets_core.WidgetValidationError):
                widgets_runtime.reconcile_widgets(
                    paths=paths,
                    target_slug="broken-widget",
                    update_processes=False,
                )

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), old_registry)
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), old_routes)

            with self.assertRaises(widgets_core.WidgetValidationError):
                widgets_runtime.reconcile_widgets(
                    paths=paths,
                    target_slug="missing-widget",
                    update_processes=False,
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
                paths=paths,
                target_slug="valid-widget",
                update_processes=False,
            )

        self.assertEqual([widget.slug for widget in result.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in result.failures], ["broken-widget"])

    def test_static_frontend_symlink_is_rejected_and_never_published(self) -> None:
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
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)
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
                    paths=paths,
                    target_slug="linked-widget",
                    update_processes=False,
                )

            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)
            registry = paths.registry_path.read_text(encoding="utf-8")
            routes = paths.routes_path.read_text(encoding="utf-8")
            snapshot = self._snapshot_files(static_root=paths.static_root)

        self.assertEqual([widget.slug for widget in result.widgets], ["valid-widget"])
        self.assertEqual([failure.slug for failure in result.failures], ["linked-widget"])
        self.assertNotIn("linked-widget", registry)
        self.assertNotIn("linked-widget", routes)
        self.assertNotIn(b"must never be public", snapshot.values())

    def test_special_static_frontend_file_is_rejected(self) -> None:
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

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)

        self.assertEqual(result.widgets, ())
        self.assertEqual([failure.slug for failure in result.failures], ["special-widget"])
        self.assertIn("regular file or directory", result.failures[0].message)

    def test_widget_processes_share_ports_preserve_assignments_and_replace_only_their_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="alpha-widget",
                title="Alpha",
                entry="index.html",
                backend=True,
            )
            self._write_backend_widget(
                paths=paths,
                slug="beta-widget",
                title="Beta",
                command="uv run server.py",
            )
            widgets = widgets_core.discover_widgets(
                widgets_root=paths.widgets_root
            ).widgets
            document = {
                "version": "0.5",
                "processes": {
                    "webapp.dashboard": {
                        "environment": ["WEBAPP_PORT=4000"],
                        "command": "webapp",
                    },
                    "webapp.__admin": {
                        "environment": ["WEBAPP_PORT=4001"],
                        "command": "admin",
                    },
                    "widget.alpha-widget": {
                        "environment": ["WIDGET_PORT=4002"],
                        "command": "old alpha",
                    },
                    "widget.removed-widget": {
                        "environment": ["WIDGET_PORT=4003"],
                        "command": "stale",
                    },
                },
            }

            reconciled, ports = widgets_runtime.reconcile_widget_processes(
                document=document,
                widgets=widgets,
                widgets_root=paths.widgets_root,
                logs_root=paths.logs_root,
            )
            reapplied, reapplied_ports = widgets_runtime.reconcile_widget_processes(
                document=reconciled,
                widgets=widgets,
                widgets_root=paths.widgets_root,
                logs_root=paths.logs_root,
            )

        processes = reconciled["processes"]
        self.assertEqual(ports, {"alpha-widget": 4002, "beta-widget": 4003})
        self.assertEqual(reapplied_ports, ports)
        self.assertEqual(reapplied, reconciled)
        self.assertEqual(
            processes["webapp.dashboard"],
            document["processes"]["webapp.dashboard"],
        )
        self.assertEqual(
            processes["webapp.__admin"],
            document["processes"]["webapp.__admin"],
        )
        self.assertNotIn("widget.removed-widget", processes)
        alpha = processes["widget.alpha-widget"]
        self.assertEqual(alpha["working_dir"], str(paths.widgets_root / "alpha-widget"))
        self.assertEqual(alpha["log_location"], str(paths.logs_root / "alpha-widget.log"))
        self.assertEqual(
            alpha["environment"],
            [
                "WIDGET_SLUG=alpha-widget",
                "WIDGET_BASE_PATH=/widgets/alpha-widget",
                "WIDGET_PORT=4002",
            ],
        )
        self.assertEqual(alpha["availability"]["restart"], "on_failure")
        self.assertIn("127.0.0.1/4002", alpha["readiness_probe"]["exec"]["command"])
        self.assertTrue(alpha["log_configuration"]["disable_json"])

    def test_widget_port_collision_reassigns_and_shared_pool_exhaustion_is_clear(self) -> None:
        backend = widgets_core.WidgetManifest(
            schema_version=1,
            slug="alpha-widget",
            title="Alpha",
            icon=None,
            frontend=widgets_core.WidgetFrontend(mode="backend", entry=None),
            backend=widgets_core.WidgetBackend(command="run"),
        )
        colliding_document = {
            "version": "0.5",
            "processes": {
                "webapp.dashboard": {"environment": ["WEBAPP_PORT=4000"]},
                "widget.alpha-widget": {"environment": ["WIDGET_PORT=4000"]},
            },
        }

        _, ports = widgets_runtime.reconcile_widget_processes(
            document=colliding_document,
            widgets=(backend,),
            widgets_root=pathlib.Path("/workspace/widgets"),
            logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
        )
        exhausted_document = {
            "version": "0.5",
            "processes": {
                f"webapp.app-{port}": {"environment": [f"WEBAPP_PORT={port}"]}
                for port in range(
                    process_supervisor.PORT_MIN,
                    process_supervisor.PORT_MAX + 1,
                )
            },
        }

        self.assertEqual(ports, {"alpha-widget": 4001})
        self.assertEqual(
            process_supervisor.used_ports(
                document={
                    "processes": {
                        "webapp": {"environment": ["WEBAPP_PORT=4000"]},
                        "widget.alpha": {"environment": ["WIDGET_PORT=4001"]},
                    }
                }
            ),
            {4000, 4001},
        )
        with self.assertRaisesRegex(
            widgets_runtime.WidgetReconciliationError,
            "no free ports in 4000-4019",
        ):
            widgets_runtime.reconcile_widget_processes(
                document=exhausted_document,
                widgets=(backend,),
                widgets_root=pathlib.Path("/workspace/widgets"),
                logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
            )

    def test_managed_ports_reject_malformed_ambiguous_and_duplicate_assignments(self) -> None:
        invalid_environments = (
            "WEBAPP_PORT=4000",
            [4000],
            ["WEBAPP_PORT"],
            ["WEBAPP_PORT=oops"],
            ["WEBAPP_PORT=3999"],
            ["WEBAPP_PORT=4000", "WEBAPP_PORT=4001"],
            ["WIDGET_PORT=4000", "WIDGET_PORT=4001"],
            ["WEBAPP_PORT=4000", "WIDGET_PORT=4001"],
        )
        for environment in invalid_environments:
            with (
                self.subTest(environment=environment),
                self.assertRaises(widgets_runtime.WidgetReconciliationError),
            ):
                widgets_runtime.reconcile_widget_processes(
                    document={
                        "version": "0.5",
                        "processes": {
                            "webapp.dashboard": {"environment": environment},
                        },
                    },
                    widgets=(),
                    widgets_root=pathlib.Path("/workspace/widgets"),
                    logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
                )

        with self.assertRaisesRegex(
            widgets_runtime.WidgetReconciliationError,
            "assigned to both 'webapp.dashboard' and 'webapp.__admin'",
        ):
            widgets_runtime.reconcile_widget_processes(
                document={
                    "version": "0.5",
                    "processes": {
                        "webapp.dashboard": {"environment": ["WEBAPP_PORT=4000"]},
                        "webapp.__admin": {"environment": ["WEBAPP_PORT=4000"]},
                    },
                },
                widgets=(),
                widgets_root=pathlib.Path("/workspace/widgets"),
                logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
            )

        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            webapps_lib.next_free_port(
                document={
                    "processes": {
                        "webapp.dashboard": {
                            "environment": ["WEBAPP_PORT=4000", "WEBAPP_PORT=4001"]
                        }
                    }
                }
            )
        self.assertIn("multiple managed port declarations", stderr.getvalue())

    def test_malformed_desired_widget_port_is_rejected_but_valid_collision_heals(self) -> None:
        backend = widgets_core.WidgetManifest(
            schema_version=1,
            slug="alpha-widget",
            title="Alpha",
            icon=None,
            frontend=widgets_core.WidgetFrontend(mode="backend", entry=None),
            backend=widgets_core.WidgetBackend(command="run"),
        )
        with self.assertRaisesRegex(
            widgets_runtime.WidgetReconciliationError,
            "out-of-range WIDGET_PORT",
        ):
            widgets_runtime.reconcile_widget_processes(
                document={
                    "version": "0.5",
                    "processes": {
                        "widget.alpha-widget": {
                            "environment": ["WIDGET_PORT=9999"]
                        }
                    },
                },
                widgets=(backend,),
                widgets_root=pathlib.Path("/workspace/widgets"),
                logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
            )

        reconciled, ports = widgets_runtime.reconcile_widget_processes(
            document={
                "version": "0.5",
                "processes": {
                    "webapp.dashboard": {"environment": ["WEBAPP_PORT=4000"]},
                    "widget.alpha-widget": {
                        "environment": ["WIDGET_PORT=4000"]
                    },
                },
            },
            widgets=(backend,),
            widgets_root=pathlib.Path("/workspace/widgets"),
            logs_root=pathlib.Path("/workspace/.config/widgets/logs"),
        )
        self.assertEqual(ports, {"alpha-widget": 4001})
        self.assertIn("WIDGET_PORT=4001", reconciled["processes"]["widget.alpha-widget"]["environment"])

    def test_backend_routes_cover_static_api_and_backend_frontend_with_unavailable_view(self) -> None:
        static_only = widgets_core.WidgetManifest(
            schema_version=1,
            slug="static-only",
            title="Static",
            icon=None,
            frontend=widgets_core.WidgetFrontend(mode="static", entry="index.html"),
            backend=None,
        )
        static_backend = widgets_core.WidgetManifest(
            schema_version=1,
            slug="static-backend",
            title="Static Backend",
            icon=None,
            frontend=widgets_core.WidgetFrontend(mode="static", entry="app.html"),
            backend=widgets_core.WidgetBackend(command="run api"),
        )
        backend_frontend = widgets_core.WidgetManifest(
            schema_version=1,
            slug="server-rendered",
            title="Server Rendered",
            icon=None,
            frontend=widgets_core.WidgetFrontend(mode="backend", entry=None),
            backend=widgets_core.WidgetBackend(command="run server"),
        )
        static_root = pathlib.Path("/workspace/.config/widgets/static")
        unavailable_path = widgets_dir / "widget-unavailable.html"

        fragment = widgets_runtime.build_widgets_caddy_fragment(
            widgets=(static_only, static_backend, backend_frontend),
            static_root=static_root,
            registry_path=pathlib.Path("/workspace/.config/widgets/registry.json"),
            backend_ports={"static-backend": 4004, "server-rendered": 4005},
            unavailable_path=unavailable_path,
        )
        unavailable_html = unavailable_path.read_text(encoding="utf-8")

        self.assertIn(
            "path /widgets/static-backend/api /widgets/static-backend/api/*",
            fragment,
        )
        self.assertIn(
            "path /widgets/server-rendered/api /widgets/server-rendered/api/*",
            fragment,
        )
        self.assertIn("path /widgets/server-rendered/*", fragment)
        self.assertIn("uri strip_prefix /widgets/static-backend", fragment)
        self.assertIn("uri strip_prefix /widgets/server-rendered", fragment)
        self.assertIn("reverse_proxy 127.0.0.1:4004", fragment)
        self.assertIn("reverse_proxy 127.0.0.1:4005", fragment)
        self.assertIn("header_up X-Forwarded-Prefix /widgets/static-backend", fragment)
        self.assertIn("header_up X-Forwarded-Host {header.X-Forwarded-Host}", fragment)
        self.assertNotIn("widget_static_backend_api_upstream_error", fragment)
        self.assertNotIn("widget_server_rendered_api_upstream_error", fragment)
        self.assertIn(
            "@widget_server_rendered_frontend_upstream_error status 5xx",
            fragment,
        )
        self.assertIn("handle_errors 5xx", fragment)
        self.assertIn("vars humr_widget_route api", fragment)
        self.assertIn("vars humr_widget_route frontend", fragment)
        self.assertIn(
            "@widget_api_dial_error vars humr_widget_route api",
            fragment,
        )
        self.assertIn(
            "@widget_frontend_dial_error vars humr_widget_route frontend",
            fragment,
        )
        self.assertIn('header Content-Type application/json', fragment)
        self.assertIn('Widget backend unavailable', fragment)
        self.assertNotIn("{err.status_code}", fragment)
        self.assertIn(str(unavailable_path.parent), fragment)
        self.assertNotIn("reverse_proxy 127.0.0.1:4000", fragment)
        self.assertNotIn("header_up Connection", fragment)
        self.assertNotIn("header_up Upgrade", fragment)
        for slug in ("static-only", "static-backend", "server-rendered"):
            self.assertIn(f"redir @widget_{slug.replace('-', '_')}_root /widgets/{slug}/ 308", fragment)
        self.assertLess(
            fragment.index("handle @widget_static_backend_api"),
            fragment.index("handle @widget_static_backend_frontend"),
        )
        self.assertLess(
            fragment.index("handle @widget_server_rendered_api"),
            fragment.index("handle @widget_server_rendered_frontend"),
        )
        self.assertIn("This Widget is unavailable", unavailable_html)
        self.assertIn("Ask your agent", unavailable_html)

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
                exit_code = widgets_cli.main(
                    argv=["apply", "--all", "--bootstrap"], paths=paths
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("reconciled 1 Widget(s)", stdout.getvalue())
        self.assertIn("skipping 'broken-widget'", stderr.getvalue())

    def test_runtime_apply_reloads_shared_project_and_bootstrap_only_seeds_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_backend_widget(
                paths=paths,
                slug="server-widget",
                title="Server",
                command="uv run server.py",
            )

            with patch.object(
                widgets_runtime.process_supervisor,
                "process_compose_project_update",
            ) as project_update:
                result = widgets_runtime.reconcile_widgets(
                    paths=paths,
                    target_slug="server-widget",
                    update_processes=True,
                )

            runtime_document = process_supervisor.load_process_compose_yaml(
                project=paths.process_project
            )
            project_update.assert_called_once_with(project=paths.process_project)

            with (
                patch.object(
                    widgets_runtime.process_supervisor,
                    "process_compose_project_update",
                ) as bootstrap_update,
                patch.object(
                    widgets_runtime.process_supervisor,
                    "wait_for_process_ready",
                ) as bootstrap_wait,
                patch.object(widgets_cli.time, "sleep") as bootstrap_sleep,
            ):
                exit_code = widgets_cli.main(
                    argv=["apply", "--all", "--bootstrap"], paths=paths
                )

        self.assertEqual([widget.slug for widget in result.widgets], ["server-widget"])
        self.assertIn("widget.server-widget", runtime_document["processes"])
        self.assertEqual(exit_code, 0)
        bootstrap_update.assert_not_called()
        bootstrap_wait.assert_not_called()
        bootstrap_sleep.assert_not_called()

    def test_targeted_backend_apply_waits_for_namespaced_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_backend_widget(
                paths=paths,
                slug="server-widget",
                title="Server",
                command="uv run server.py",
            )
            with (
                patch.object(
                    widgets_runtime.process_supervisor,
                    "process_compose_project_update",
                ) as project_update,
                patch.object(
                    widgets_runtime.process_supervisor,
                    "wait_for_process_ready",
                    return_value={
                        "name": "widget.server-widget",
                        "status": "Running",
                        "is_ready": "Ready",
                    },
                ) as wait_for_ready,
                patch.object(widgets_cli.time, "sleep") as settle_sleep,
            ):
                exit_code = widgets_cli.main(
                    argv=["apply", "server-widget", "--timeout", "9"],
                    paths=paths,
                )

        self.assertEqual(exit_code, 0)
        project_update.assert_called_once_with(project=paths.process_project)
        settle_sleep.assert_called_once_with(3)
        wait_for_ready.assert_called_once_with(
            project=paths.process_project,
            process_name="widget.server-widget",
            timeout=9,
        )

    def test_targeted_backend_apply_reports_terminal_or_timed_out_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_backend_widget(
                paths=paths,
                slug="server-widget",
                title="Server",
                command="uv run server.py",
            )
            stderr = io.StringIO()
            with (
                patch.object(
                    widgets_runtime.process_supervisor,
                    "process_compose_project_update",
                ),
                patch.object(
                    widgets_runtime.process_supervisor,
                    "wait_for_process_ready",
                    return_value={
                        "name": "widget.server-widget",
                        "status": "Error",
                        "is_ready": "Not Ready",
                        "exit_code": 17,
                        "restarts": 5,
                    },
                ),
                patch.object(widgets_cli.time, "sleep") as settle_sleep,
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = widgets_cli.main(
                    argv=["apply", "server-widget", "--timeout", "3"],
                    paths=paths,
                )

        self.assertEqual(exit_code, 1)
        settle_sleep.assert_called_once_with(3)
        self.assertIn("did not become ready within 3s", stderr.getvalue())
        self.assertIn("status=Error", stderr.getvalue())
        self.assertIn("is_ready=Not Ready", stderr.getvalue())
        self.assertIn("exit_code=17", stderr.getvalue())
        self.assertIn("restarts=5", stderr.getvalue())
        self.assertIn("widgets logs server-widget", stderr.getvalue())

    def test_readiness_wait_stops_on_terminal_state_and_reports_missing_timeout(self) -> None:
        project = process_supervisor.ProcessComposeProject(
            config_dir=pathlib.Path("/tmp/widgets-readiness-test"),
            port="9957",
            lock_name=".lock",
            required_dirs=(),
        )
        with (
            patch.object(
                widgets_runtime.process_supervisor,
                "process_compose_state_for",
                side_effect=[
                    {
                        "name": "widget.server-widget",
                        "status": "Running",
                        "is_ready": "Not Ready",
                    },
                    {
                        "name": "widget.server-widget",
                        "status": "Error",
                        "is_ready": "Not Ready",
                        "exit_code": 9,
                    },
                ],
            ),
            patch.object(
                widgets_runtime.process_supervisor.time,
                "monotonic",
                side_effect=[0.0, 0.1, 0.2],
            ),
            patch.object(widgets_runtime.process_supervisor.time, "sleep") as sleep,
        ):
            terminal = widgets_runtime.process_supervisor.wait_for_process_ready(
                project=project,
                process_name="widget.server-widget",
                timeout=5,
            )

        self.assertEqual(terminal["status"], "Error")
        self.assertEqual(terminal["exit_code"], 9)
        sleep.assert_called_once_with(0.5)

        with (
            patch.object(
                widgets_runtime.process_supervisor,
                "process_compose_state_for",
                return_value=None,
            ),
            patch.object(
                widgets_runtime.process_supervisor.time,
                "monotonic",
                side_effect=[10.0, 11.0],
            ),
        ):
            timed_out = widgets_runtime.process_supervisor.wait_for_process_ready(
                project=project,
                process_name="widget.missing-widget",
                timeout=1,
            )

        self.assertEqual(
            timed_out,
            {
                "name": "widget.missing-widget",
                "status": "unknown",
                "is_ready": "Unknown",
            },
        )

    def test_static_target_and_apply_all_do_not_wait_for_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_static_widget(
                paths=paths,
                slug="static-widget",
                title="Static",
                entry="index.html",
                backend=False,
            )
            with (
                patch.object(
                    widgets_runtime.process_supervisor,
                    "process_compose_project_update",
                ),
                patch.object(
                    widgets_runtime.process_supervisor,
                    "wait_for_process_ready",
                ) as wait_for_ready,
                patch.object(widgets_cli.time, "sleep") as settle_sleep,
            ):
                static_exit = widgets_cli.main(
                    argv=["apply", "static-widget"],
                    paths=paths,
                )
                all_exit = widgets_cli.main(
                    argv=["apply", "--all"],
                    paths=paths,
                )

        self.assertEqual(static_exit, 0)
        self.assertEqual(all_exit, 0)
        self.assertEqual(settle_sleep.call_args_list, [call(3), call(3)])
        wait_for_ready.assert_not_called()
        parsed = widgets_cli.build_parser().parse_args(["apply", "static-widget"])
        self.assertEqual(
            parsed.timeout,
            widgets_runtime.DEFAULT_APPLY_TIMEOUT_SECONDS,
        )

    def test_logs_tails_the_widget_log_with_requested_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            paths.logs_root.mkdir(parents=True)
            log_path = paths.logs_root / "server-widget.log"
            log_path.write_text("started\n", encoding="utf-8")

            with (
                patch.object(
                    widgets_cli.os,
                    "execvp",
                    side_effect=SystemExit(0),
                ) as execvp,
                self.assertRaises(SystemExit),
            ):
                widgets_cli.main(
                    argv=["logs", "server-widget", "-f", "-n", "12"],
                    paths=paths,
                )

        execvp.assert_called_once_with(
            "tail",
            ["tail", "-f", "-n", "12", str(log_path)],
        )

    def test_runtime_reload_failure_rolls_back_yaml_and_all_other_derived_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_dir = self._write_backend_widget(
                paths=paths,
                slug="server-widget",
                title="Server",
                command="uv run old-server.py",
            )
            widgets_runtime.reconcile_widgets(
                paths=paths,
                target_slug=None,
                update_processes=False,
            )
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)
            old_process_yaml = paths.process_project.yaml_path.read_bytes()
            manifest = json.loads(
                (widget_dir / "widget.json").read_text(encoding="utf-8")
            )
            manifest["backend"]["command"] = "uv run new-server.py"
            (widget_dir / "widget.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            observed_process_documents: list[str] = []

            def reload_project(*, project: object) -> None:
                del project
                observed_process_documents.append(
                    paths.process_project.yaml_path.read_text(encoding="utf-8")
                )
                if len(observed_process_documents) == 1:
                    raise subprocess.CalledProcessError(
                        returncode=1, cmd=["process-compose", "project", "update"]
                    )

            with (
                patch.object(
                    widgets_runtime.process_supervisor,
                    "process_compose_project_update",
                    side_effect=reload_project,
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                widgets_runtime.reconcile_widgets(
                    paths=paths,
                    target_slug="server-widget",
                    update_processes=True,
                )

            self.assertIn("new-server.py", observed_process_documents[0])
            self.assertIn("old-server.py", observed_process_documents[1])
            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )
            self.assertEqual(
                paths.process_project.yaml_path.read_bytes(), old_process_yaml
            )

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
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)

            self.assertEqual(paths.registry_path.read_text(encoding="utf-8"), "old registry\n")
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), "old routes\n")

    def test_later_publication_failure_rolls_back_snapshot_registry_routes_and_yaml(self) -> None:
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
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)
            old_process_yaml = paths.process_project.yaml_path.read_bytes()
            self._write_static_widget(
                paths=paths,
                slug="second-widget",
                title="Second",
                entry="public/app.html",
                backend=False,
            )
            real_replace = widgets_runtime.os.replace
            replace_calls = 0

            def fail_eighth_replace(*, src: pathlib.Path, dst: pathlib.Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 8:
                    raise OSError("injected process YAML publication failure")
                real_replace(src=src, dst=dst)

            with (
                patch.object(
                    widgets_runtime.os,
                    "replace",
                    side_effect=fail_eighth_replace,
                ),
                self.assertRaisesRegex(
                    OSError, "injected process YAML publication failure"
                ),
            ):
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)

            self.assertEqual(paths.registry_path.read_bytes(), old_registry)
            self.assertEqual(paths.routes_path.read_bytes(), old_routes)
            self.assertEqual(
                self._snapshot_files(static_root=paths.static_root), old_snapshot
            )
            self.assertEqual(
                paths.process_project.yaml_path.read_bytes(), old_process_yaml
            )
            self.assertEqual(list(root.rglob("*.backup")), [])
            self.assertEqual(list(root.rglob("*.stage")), [])

            result = widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)

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
                widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)

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
                    paths=paths,
                    slug="delete-me",
                    confirmed=False,
                    update_processes=False,
                )
            result = widgets_runtime.delete_widget(
                paths=paths,
                slug="delete-me",
                confirmed=True,
                update_processes=False,
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

    def test_delete_backend_removes_its_process_and_log_but_preserves_webapps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            self._write_backend_widget(
                paths=paths,
                slug="delete-backend",
                title="Delete Backend",
                command="uv run server.py",
            )
            widgets_runtime.reconcile_widgets(
                paths=paths,
                target_slug=None,
                update_processes=False,
            )
            document = process_supervisor.load_process_compose_yaml(
                project=paths.process_project
            )
            document["processes"]["webapp.dashboard"] = {
                "command": "uv run dashboard.py",
                "environment": ["WEBAPP_PORT=4001"],
            }
            process_supervisor.save_process_compose_yaml(
                project=paths.process_project,
                document=document,
            )
            log_path = paths.logs_root / "delete-backend.log"
            log_path.write_text("started\n", encoding="utf-8")

            result = widgets_runtime.delete_widget(
                paths=paths,
                slug="delete-backend",
                confirmed=True,
                update_processes=False,
            )
            reconciled = process_supervisor.load_process_compose_yaml(
                project=paths.process_project
            )

        self.assertEqual(result.widgets, ())
        self.assertNotIn("widget.delete-backend", reconciled["processes"])
        self.assertEqual(
            reconciled["processes"]["webapp.dashboard"],
            document["processes"]["webapp.dashboard"],
        )
        self.assertFalse(log_path.exists())

    def test_delete_cleanup_failure_is_committed_and_exact_retry_finishes_residuals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_path = self._write_backend_widget(
                paths=paths,
                slug="cleanup-widget",
                title="Cleanup",
                command="uv run server.py",
            )
            widgets_runtime.reconcile_widgets(
                paths=paths,
                target_slug=None,
                update_processes=False,
            )
            log_path = paths.logs_root / "cleanup-widget.log"
            log_path.write_text("backend output\n", encoding="utf-8")
            tombstone_path = paths.tombstones_root / "cleanup-widget"
            real_rmtree = widgets_runtime.shutil.rmtree

            def fail_residual_cleanup(path: pathlib.Path) -> None:
                if pathlib.Path(path) == tombstone_path:
                    raise OSError("injected residual cleanup failure")
                real_rmtree(path)

            with (
                patch.object(
                    widgets_runtime.shutil,
                    "rmtree",
                    side_effect=fail_residual_cleanup,
                ),
                self.assertRaisesRegex(
                    widgets_runtime.WidgetDeletionCommittedError,
                    "deletion is committed with residual cleanup",
                ),
            ):
                widgets_runtime.delete_widget(
                    paths=paths,
                    slug="cleanup-widget",
                    confirmed=True,
                    update_processes=False,
                )

            committed_document = process_supervisor.load_process_compose_yaml(
                project=paths.process_project
            )
            committed_routes = paths.routes_path.read_text(encoding="utf-8")
            self.assertFalse(widget_path.exists())
            self.assertFalse(log_path.exists())
            self.assertTrue((tombstone_path / "source").is_dir())
            self.assertEqual(
                (tombstone_path / "backend.log").read_text(encoding="utf-8"),
                "backend output\n",
            )
            self.assertNotIn(
                "widget.cleanup-widget",
                committed_document["processes"],
            )
            self.assertNotIn("cleanup-widget", committed_routes)

            retry_result = widgets_runtime.delete_widget(
                paths=paths,
                slug="cleanup-widget",
                confirmed=True,
                update_processes=False,
            )

        self.assertEqual(retry_result.widgets, ())
        self.assertFalse(tombstone_path.exists())

    def test_delete_retry_after_precommit_interruption_reconciles_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = self._paths(root=pathlib.Path(temporary_dir))
            widget_path = self._write_backend_widget(
                paths=paths,
                slug="interrupted-widget",
                title="Interrupted",
                command="uv run server.py",
            )
            widgets_runtime.reconcile_widgets(
                paths=paths,
                target_slug=None,
                update_processes=False,
            )
            log_path = paths.logs_root / "interrupted-widget.log"
            log_path.write_text("preserve until commit\n", encoding="utf-8")
            stale_registry = paths.registry_path.read_bytes()
            stale_routes = paths.routes_path.read_bytes()
            stale_process_yaml = paths.process_project.yaml_path.read_bytes()
            self.assertIn(b"interrupted-widget", stale_registry)
            self.assertIn(b"interrupted-widget", stale_routes)
            self.assertIn(b"widget.interrupted-widget", stale_process_yaml)

            tombstone_path = paths.tombstones_root / "interrupted-widget"
            tombstone_path.mkdir(parents=True)
            os.replace(src=widget_path, dst=tombstone_path / "source")
            os.replace(src=log_path, dst=tombstone_path / "backend.log")

            with (
                patch.object(
                    widgets_runtime,
                    "_reconcile_widgets_unlocked",
                    side_effect=RuntimeError("injected retry reconciliation failure"),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "injected retry reconciliation failure",
                ),
            ):
                widgets_runtime.delete_widget(
                    paths=paths,
                    slug="interrupted-widget",
                    confirmed=True,
                    update_processes=True,
                )

            self.assertTrue((tombstone_path / "source").is_dir())
            self.assertEqual(
                (tombstone_path / "backend.log").read_text(encoding="utf-8"),
                "preserve until commit\n",
            )
            self.assertEqual(paths.registry_path.read_bytes(), stale_registry)
            self.assertEqual(paths.routes_path.read_bytes(), stale_routes)
            self.assertEqual(
                paths.process_project.yaml_path.read_bytes(),
                stale_process_yaml,
            )

            with patch.object(
                widgets_runtime.process_supervisor,
                "process_compose_project_update",
            ) as project_update:
                retry_result = widgets_runtime.delete_widget(
                    paths=paths,
                    slug="interrupted-widget",
                    confirmed=True,
                    update_processes=True,
                )

            reconciled_registry = paths.registry_path.read_text(encoding="utf-8")
            reconciled_routes = paths.routes_path.read_text(encoding="utf-8")
            reconciled_processes = process_supervisor.load_process_compose_yaml(
                project=paths.process_project
            )["processes"]

        self.assertEqual(retry_result.widgets, ())
        project_update.assert_called_once_with(project=paths.process_project)
        self.assertNotIn("interrupted-widget", reconciled_registry)
        self.assertNotIn("interrupted-widget", reconciled_routes)
        self.assertNotIn("widget.interrupted-widget", reconciled_processes)
        self.assertFalse(tombstone_path.exists())
        self.assertFalse(widget_path.exists())
        self.assertFalse(log_path.exists())

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
            widgets_runtime.reconcile_widgets(paths=paths, target_slug=None, update_processes=False)
            log_path = paths.logs_root / "restore-me.log"
            log_path.write_text("restore this log\n", encoding="utf-8")
            old_registry = paths.registry_path.read_bytes()
            old_routes = paths.routes_path.read_bytes()
            old_snapshot = self._snapshot_files(static_root=paths.static_root)
            discovered_during_delete: list[str] = []

            def fail_publication(
                *,
                artifacts: object,
                publish_hook: object,
                rollback_hook: object,
            ) -> None:
                del artifacts, publish_hook, rollback_hook
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
                    paths=paths,
                    slug="restore-me",
                    confirmed=True,
                    update_processes=False,
                )

            self.assertTrue(widget_path.is_dir())
            self.assertTrue((widget_path / "widget.json").is_file())
            self.assertEqual(
                log_path.read_text(encoding="utf-8"),
                "restore this log\n",
            )
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
                    paths=paths,
                    slug="__admin",
                    confirmed=True,
                    update_processes=False,
                )
            with self.assertRaisesRegex(
                widgets_core.WidgetValidationError, "symbolic-link"
            ):
                widgets_runtime.delete_widget(
                    paths=paths,
                    slug="linked-widget",
                    confirmed=True,
                    update_processes=False,
                )

            self.assertTrue(outside.exists())

    def test_webapps_and_widgets_own_separate_imported_fragments(self) -> None:
        caddyfile = (
            _runtime_dir() / "http_router" / "Caddyfile"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            webapps_lib.WEBAPP_ROUTES_PATH,
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
            document = {
                "processes": {
                    "webapp.dashboard": {
                        "environment": ["WEBAPP_PORT=4000"],
                    },
                    "widget.private-runtime": {
                        "environment": ["WIDGET_PORT=4001"],
                    },
                }
            }

            with (
                patch.object(webapps_lib, "WEBAPP_ROUTES_PATH", webapps_fragment),
                patch.object(webapps_lib, "WEBAPP_PROJECTS_DIR", root / "projects"),
                patch.object(webapps_lib, "WEBAPP_LOGS_DIR", root / "logs"),
                patch.dict(
                    webapps_lib.os.environ,
                    {"HUMR_PUBLIC_HOSTNAME": "agent.example.com"},
                    clear=True,
                ),
            ):
                webapps_lib.regenerate_webapp_routes(document=document)

            generated_webapps = webapps_fragment.read_text(encoding="utf-8")
            preserved_widgets = widgets_fragment.read_text(encoding="utf-8")

        self.assertIn("dashboard-agent.example.com", generated_webapps)
        self.assertNotIn("private-runtime", generated_webapps)
        self.assertEqual(preserved_widgets, "Widget routes stay intact\n")

    def test_webapps_cli_hides_widget_processes_and_refuses_widget_names(self) -> None:
        document = {
            "processes": {
                "webapp.dashboard": {"environment": ["WEBAPP_PORT=4000"]},
                "widget.private-runtime": {
                    "environment": ["WIDGET_PORT=4001"]
                },
            }
        }
        stdout = io.StringIO()
        with (
            patch.object(
                webapps_cli.process_supervisor,
                "load_process_compose_yaml",
                return_value=document,
            ),
            patch.object(
                webapps_cli.process_supervisor,
                "process_compose_states",
                return_value=[
                    {
                        "name": "webapp.dashboard",
                        "status": "Running",
                        "is_ready": "Ready",
                    },
                    {
                        "name": "widget.private-runtime",
                        "status": "Running",
                        "is_ready": "Ready",
                    },
                ],
            ),
            patch.object(webapps_cli.webapps_lib, "is_routed", return_value=False),
            contextlib.redirect_stdout(stdout),
        ):
            webapps_cli.cmd_list(types.SimpleNamespace())

        self.assertIn("dashboard", stdout.getvalue())
        self.assertNotIn("private-runtime", stdout.getvalue())
        with self.assertRaises(SystemExit):
            webapps_cli.cmd_stop(types.SimpleNamespace(slug="widget.private-runtime"))

    def test_webapps_admin_hides_widget_processes_from_all_endpoints(self) -> None:
        fastapi = types.ModuleType("fastapi")
        fastapi_responses = types.ModuleType("fastapi.responses")

        class FakeFastAPI:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get(self, _path: str):
                def decorate(function: object) -> object:
                    return function

                return decorate

        class FakeHTTPException(Exception):
            def __init__(self, status_code: int) -> None:
                super().__init__(status_code)
                self.status_code = status_code

        class FakePlainTextResponse:
            def __init__(self, content: str, media_type: str) -> None:
                self.content = content
                self.media_type = media_type

        class FakeJSONResponse:
            def __init__(self, content: object) -> None:
                self.content = content

        fastapi.FastAPI = FakeFastAPI
        fastapi.HTTPException = FakeHTTPException
        fastapi.Request = object
        fastapi_responses.JSONResponse = FakeJSONResponse
        fastapi_responses.PlainTextResponse = FakePlainTextResponse
        fastapi_responses.Response = object
        with patch.dict(
            sys.modules,
            {
                "fastapi": fastapi,
                "fastapi.responses": fastapi_responses,
            },
        ):
            admin_server = _load_module(
                name="webapps_admin_for_widgets_test",
                path=webapps_dir / "admin" / "server.py",
            )

        document = {
            "processes": {
                "webapp.dashboard": {"environment": ["WEBAPP_PORT=4000"]},
                "widget.private-runtime": {
                    "environment": ["WIDGET_PORT=4001"]
                },
            }
        }
        with (
            patch.object(
                admin_server,
                "_load_webapp_processes",
                return_value=webapps_lib.webapp_processes(document=document),
            ),
            patch.object(
                admin_server.process_supervisor,
                "process_compose_states",
                return_value=[],
            ),
            patch.object(admin_server.webapps_lib, "is_routed", return_value=False),
        ):
            listing = admin_server.list_webapps()

        self.assertEqual(
            [item["slug"] for item in listing["items"]],
            ["dashboard"],
        )
        with patch.object(
            admin_server,
            "_load_webapp_processes",
            return_value=webapps_lib.webapp_processes(document=document),
        ):
            with self.assertRaises(FakeHTTPException) as detail_error:
                admin_server.detail("widget.private-runtime")
            with self.assertRaises(FakeHTTPException) as logs_error:
                admin_server.logs(
                    "widget.private-runtime",
                    request=types.SimpleNamespace(query_params={}),
                )
        self.assertEqual(detail_error.exception.status_code, 404)
        self.assertEqual(logs_error.exception.status_code, 404)

    def test_boot_docker_and_profile_wire_widgets_before_supervisors(self) -> None:
        hermes_agent = _runtime_dir().parent
        webui_script = (_runtime_dir() / "webui.sh").read_text(encoding="utf-8")
        dockerfile = (hermes_agent / "Dockerfile").read_text(encoding="utf-8")
        profile = json.loads(
            (_runtime_dir() / "hermes-nono-profile.json").read_text(encoding="utf-8")
        )

        self.assertIn("widgets apply --all --bootstrap", webui_script)
        self.assertLess(webui_script.index("reconcile_widgets\n"), webui_script.index("start_system_process_compose\n"))
        self.assertIn("start_app_workloads_compose", webui_script)
        self.assertIn("/app-workloads/process-compose.yaml", webui_script)
        self.assertNotIn("/webapps/process-compose.yaml", webui_script)
        self.assertIn("/workspace/.config/caddy/webapps.caddy", webui_script)
        self.assertIn("/workspace/.config/caddy/widgets.caddy", webui_script)
        self.assertIn("ln -sf /opt/humr/runtime/widgets/widgets /opt/humr/bin/widgets", dockerfile)
        self.assertIn("COPY humr_runtime /opt/humr/runtime", dockerfile)
        self.assertTrue((widgets_dir / "widgets").stat().st_mode & 0o111)
        self.assertIn("/opt/humr/runtime/widgets/widgets", profile["filesystem"]["read_file"])
        self.assertIn("/opt/humr/runtime/widgets/widgets_core.py", profile["filesystem"]["read_file"])
        self.assertIn("/opt/humr/runtime/widgets/widgets_runtime.py", profile["filesystem"]["read_file"])
        self.assertIn(
            "/opt/humr/runtime/process_supervisor/process_supervisor.py",
            profile["filesystem"]["read_file"],
        )
        self.assertIn(
            "/opt/humr/runtime/http_router/Caddyfile",
            profile["filesystem"]["read_file"],
        )
        self.assertIn(
            "/opt/humr/runtime/widgets/widget-unavailable.html",
            profile["filesystem"]["read_file"],
        )
        self.assertIn("/opt/humr/bin/widgets", profile["filesystem"]["read_file"])


if __name__ == "__main__":
    unittest.main()
