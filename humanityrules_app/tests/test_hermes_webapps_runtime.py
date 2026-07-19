"""Tests for the Hermes webapps process-compose runtime contract."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _runtime_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "template_repos" / "hermes_agent" / "humr_runtime" / "webapps"


def _humr_runtime_dir() -> pathlib.Path:
    return _runtime_dir().parent


def _load_runtime_module(name: str, filename: str) -> types.ModuleType:
    webapps_dir = _runtime_dir()
    if str(webapps_dir) not in sys.path:
        sys.path.insert(0, str(webapps_dir))

    script_path = webapps_dir / filename
    loader = importlib.machinery.SourceFileLoader(name, str(script_path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


webapps_lib = _load_runtime_module(name="webapps_lib", filename="webapps_lib.py")
webapps_cli = _load_runtime_module(name="webapps_cli_under_test", filename="webapps")
system_process_compose_seed = _load_runtime_module(
    name="system_process_compose_seed_under_test",
    filename="system_process_compose_seed.py",
)
seed_example_webapps = _load_runtime_module(
    name="seed_example_webapps_under_test",
    filename="seed_example_webapps.py",
)


class DummyLock:
    def __init__(self, project: object) -> None:
        self.project = project

    def __enter__(self) -> "DummyLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class TestHermesWebappsRuntimeContract(unittest.TestCase):

    def test_webapps_and_system_process_compose_paths_are_split(self) -> None:
        self.assertEqual(
            str(webapps_lib.SYSTEM_PROJECT.yaml_path),
            "/workspace/.config/process-compose/system/process-compose.yaml",
        )
        self.assertEqual(
            str(webapps_lib.WEBAPPS_PROJECT.yaml_path),
            "/workspace/.config/process-compose/webapps/process-compose.yaml",
        )
        self.assertEqual(
            str(webapps_lib.SYSTEM_PROJECT.lock_file),
            "/workspace/.config/process-compose/system/.system.lock",
        )
        self.assertEqual(
            str(webapps_lib.WEBAPPS_PROJECT.lock_file),
            "/workspace/.config/process-compose/webapps/.webapps.lock",
        )
        self.assertEqual(webapps_lib.WEBAPPS_PROJECT.port, "9957")
        self.assertEqual(webapps_lib.SYSTEM_PROJECT.port, "9956")

    def test_webapps_runtime_does_not_export_generic_yaml_helpers(self) -> None:
        self.assertFalse(hasattr(webapps_lib, "PROCESS_COMPOSE_YAML"))
        self.assertFalse(hasattr(webapps_lib, "PROCESS_COMPOSE_DIR"))
        self.assertFalse(hasattr(webapps_lib, "PROCESS_COMPOSE_PORT"))
        self.assertFalse(hasattr(webapps_lib, "LOCK_FILE"))
        self.assertFalse(hasattr(webapps_lib, "PROCESS_COMPOSE_ROOT"))
        self.assertFalse(hasattr(webapps_lib, "WEBAPPS_PROCESS_COMPOSE_DIR"))
        self.assertFalse(hasattr(webapps_lib, "WEBAPPS_PROCESS_COMPOSE_YAML"))
        self.assertFalse(hasattr(webapps_lib, "WEBAPPS_PROCESS_COMPOSE_PORT"))
        self.assertFalse(hasattr(webapps_lib, "WEBAPPS_LOCK_FILE"))
        self.assertFalse(hasattr(webapps_lib, "SYSTEM_PROCESS_COMPOSE_DIR"))
        self.assertFalse(hasattr(webapps_lib, "SYSTEM_PROCESS_COMPOSE_YAML"))
        self.assertFalse(hasattr(webapps_lib, "SYSTEM_PROCESS_COMPOSE_PORT"))
        self.assertFalse(hasattr(webapps_lib, "SYSTEM_LOCK_FILE"))
        self.assertFalse(hasattr(webapps_lib, "CADDY_CONFIG_DIR"))
        self.assertFalse(hasattr(webapps_lib, "CADDY_ROUTES"))
        self.assertFalse(hasattr(webapps_lib, "Lock"))
        self.assertFalse(hasattr(webapps_lib, "load_yaml"))
        self.assertFalse(hasattr(webapps_lib, "save_yaml"))
        self.assertFalse(hasattr(webapps_lib, "WebappsLock"))
        self.assertFalse(hasattr(webapps_lib, "SystemLock"))
        self.assertFalse(hasattr(webapps_lib, "load_webapps_yaml"))
        self.assertFalse(hasattr(webapps_lib, "save_webapps_yaml"))
        self.assertFalse(hasattr(webapps_lib, "load_system_yaml"))
        self.assertFalse(hasattr(webapps_lib, "save_system_yaml"))

    def test_webapp_host_route_uses_dash_join_for_dashed_slug(self) -> None:
        route = webapps_lib.route_block_webapp_host(
            slug="my-dash-board",
            port=4001,
            base_host="wolfie.humr.io",
        )

        self.assertIn(
            "@webapp_my_dash_board header X-Forwarded-Host my-dash-board-wolfie.humr.io\n",
            route,
        )

    def test_url_for_uses_dash_join_for_dashed_slug(self) -> None:
        with patch.dict(webapps_lib.os.environ, {"HUMR_PUBLIC_HOSTNAME": "wolfie.humr.io"}, clear=True):
            url = webapps_lib.url_for(slug="my-dash-board")

        self.assertEqual(url, "https://my-dash-board-wolfie.humr.io/")

    def test_webapps_cli_uses_webapps_daemon_for_project_update(self) -> None:
        with patch.object(webapps_lib.subprocess, "run") as run:
            webapps_lib.process_compose_project_update(project=webapps_lib.WEBAPPS_PROJECT)

        run.assert_called_once_with(
            [
                "process-compose",
                "--address",
                "127.0.0.1",
                "--port",
                "9957",
                "project",
                "update",
                "--config",
                "/workspace/.config/process-compose/webapps/process-compose.yaml",
            ],
            check=True,
            capture_output=False,
            text=True,
        )

    def test_webui_starts_process_compose_with_explicit_internal_log_files(self) -> None:
        script = (_humr_runtime_dir() / "webui.sh").read_text()

        self.assertIn('--log-file "${PROCESS_COMPOSE_ROOT}/system/process-compose.log"', script)
        self.assertIn('--log-file "${PROCESS_COMPOSE_ROOT}/webapps/process-compose.log"', script)
        self.assertIn('--config "${PROCESS_COMPOSE_ROOT}/system/process-compose.yaml"', script)
        self.assertIn('--config "${PROCESS_COMPOSE_ROOT}/webapps/process-compose.yaml"', script)
        self.assertNotIn("SYSTEM_PROCESS_COMPOSE_YAML=", script)
        self.assertNotIn("WEBAPPS_PROCESS_COMPOSE_YAML=", script)
        self.assertNotIn("SYSTEM_PROCESS_COMPOSE_LOG=", script)
        self.assertNotIn("WEBAPPS_PROCESS_COMPOSE_LOG=", script)

    def test_webapps_reload_regenerates_routes_and_updates_project(self) -> None:
        doc = {
            "version": "0.5",
            "processes": {
                "dashboard": {
                    "command": "uv run app",
                },
            },
        }

        with (
            patch.object(webapps_cli, "ProcessComposeLock", DummyLock),
            patch.object(webapps_cli, "load_process_compose_yaml", return_value=doc) as load_yaml,
            patch.object(webapps_cli, "regenerate_routes") as regenerate_routes,
            patch.object(webapps_cli, "process_compose_project_update") as project_update,
            patch("builtins.print"),
        ):
            webapps_cli.cmd_reload(types.SimpleNamespace())

        load_yaml.assert_called_once_with(project=webapps_lib.WEBAPPS_PROJECT)
        regenerate_routes.assert_called_once_with(doc)
        project_update.assert_called_once_with(project=webapps_lib.WEBAPPS_PROJECT)

    def test_webapps_unregister_removes_supervision_without_deleting_artifacts(self) -> None:
        saved_calls = []
        doc = {
            "version": "0.5",
            "processes": {
                "dashboard": {
                    "command": "uv run app",
                },
                "reports": {
                    "command": "uv run reports",
                },
            },
        }

        def save_doc(*, project: object, doc: dict) -> None:
            saved_calls.append((project, doc))

        with (
            patch.object(webapps_cli, "ProcessComposeLock", DummyLock),
            patch.object(webapps_cli, "load_process_compose_yaml", return_value=doc),
            patch.object(webapps_cli, "save_process_compose_yaml", side_effect=save_doc) as save_yaml,
            patch.object(webapps_cli, "regenerate_routes") as regenerate_routes,
            patch.object(webapps_cli, "process_compose_project_update") as project_update,
            patch.object(webapps_cli.shutil, "rmtree") as rmtree,
            patch("builtins.print"),
        ):
            webapps_cli.cmd_unregister(types.SimpleNamespace(slug="dashboard"))

        save_yaml.assert_called_once()
        self.assertEqual(len(saved_calls), 1)
        project, saved_doc = saved_calls[0]
        self.assertIs(project, webapps_lib.WEBAPPS_PROJECT)
        self.assertEqual(set(saved_doc["processes"]), {"reports"})
        regenerate_routes.assert_called_once_with(saved_doc)
        project_update.assert_called_once_with(project=webapps_lib.WEBAPPS_PROJECT)
        rmtree.assert_not_called()

    def test_webapps_create_registers_stopped_entry_with_env_without_starting(self) -> None:
        saved_calls = []

        def save_doc(*, project: object, doc: dict) -> None:
            saved_calls.append((project, doc))

        with tempfile.TemporaryDirectory() as tmpdir:
            args = types.SimpleNamespace(
                slug="dashboard",
                command="uv run app",
                cwd=tmpdir,
                env=["HERMES_WEB_DIST=/workspace/dist", "PYTHONPATH=/workspace/lib"],
                if_missing=False,
                bootstrap_enabled=False,
            )
            with (
                patch.object(webapps_cli, "ProcessComposeLock", DummyLock),
                patch.object(
                    webapps_cli,
                    "load_process_compose_yaml",
                    return_value={"version": "0.5", "processes": {}},
                ),
                patch.object(webapps_cli, "save_process_compose_yaml", side_effect=save_doc) as save_yaml,
                patch.object(webapps_cli, "regenerate_routes") as regenerate_routes,
                patch.object(webapps_cli, "process_compose_project_update") as project_update,
                patch.object(webapps_cli, "wait_for_ready") as wait_for_ready,
                patch("builtins.print"),
            ):
                webapps_cli.cmd_create(args)

        save_yaml.assert_called_once()
        self.assertEqual(len(saved_calls), 1)
        project, saved_doc = saved_calls[0]
        self.assertIs(project, webapps_lib.WEBAPPS_PROJECT)
        entry = saved_doc["processes"]["dashboard"]
        self.assertEqual(entry["command"], "uv run app")
        self.assertTrue(entry["disabled"])
        self.assertEqual(entry["log_location"], "/workspace/webapps/logs/dashboard.log")
        self.assertEqual(
            entry["log_configuration"],
            {
                "disable_json": True,
                "no_metadata": True,
                "no_color": True,
                "fields_order": ["message"],
                "flush_each_line": True,
            },
        )
        self.assertEqual(
            entry["environment"],
            [
                "WEBAPP_PORT=4000",
                "HERMES_WEB_DIST=/workspace/dist",
                "PYTHONPATH=/workspace/lib",
            ],
        )
        regenerate_routes.assert_called_once_with(saved_doc)
        project_update.assert_not_called()
        wait_for_ready.assert_not_called()

    def test_webapps_create_bootstrap_enabled_keeps_admin_enabled_without_starting(self) -> None:
        saved_calls = []

        def save_doc(*, project: object, doc: dict) -> None:
            saved_calls.append((project, doc))

        with tempfile.TemporaryDirectory() as tmpdir:
            args = types.SimpleNamespace(
                slug="__admin",
                command="python -m admin",
                cwd=tmpdir,
                env=[],
                if_missing=False,
                bootstrap_enabled=True,
            )
            with (
                patch.object(webapps_cli, "ProcessComposeLock", DummyLock),
                patch.object(
                    webapps_cli,
                    "load_process_compose_yaml",
                    return_value={"version": "0.5", "processes": {}},
                ),
                patch.object(webapps_cli, "save_process_compose_yaml", side_effect=save_doc),
                patch.object(webapps_cli, "regenerate_routes"),
                patch.object(webapps_cli, "process_compose_project_update") as project_update,
                patch.object(webapps_cli, "wait_for_ready") as wait_for_ready,
                patch("builtins.print"),
            ):
                webapps_cli.cmd_create(args)

        self.assertEqual(len(saved_calls), 1)
        _project, saved_doc = saved_calls[0]
        self.assertNotIn("disabled", saved_doc["processes"]["__admin"])
        project_update.assert_not_called()
        wait_for_ready.assert_not_called()

    def test_webapps_set_env_on_stopped_entry_does_not_update_project(self) -> None:
        saved_calls = []
        doc = {
            "version": "0.5",
            "processes": {
                "dashboard": {
                    "command": "uv run app",
                    "disabled": True,
                    "environment": ["WEBAPP_PORT=4000"],
                },
            },
        }

        def save_doc(*, project: object, doc: dict) -> None:
            saved_calls.append((project, doc))

        with (
            patch.object(webapps_cli, "ProcessComposeLock", DummyLock),
            patch.object(webapps_cli, "load_process_compose_yaml", return_value=doc),
            patch.object(webapps_cli, "save_process_compose_yaml", side_effect=save_doc),
            patch.object(webapps_cli, "process_compose_project_update") as project_update,
            patch("builtins.print"),
        ):
            webapps_cli.cmd_set_env(types.SimpleNamespace(slug="dashboard", kv=["HERMES_WEB_DIST=/workspace/dist"]))

        self.assertEqual(len(saved_calls), 1)
        _project, saved_doc = saved_calls[0]
        self.assertEqual(
            saved_doc["processes"]["dashboard"]["environment"],
            [
                "WEBAPP_PORT=4000",
                "HERMES_WEB_DIST=/workspace/dist",
            ],
        )
        project_update.assert_not_called()

    def test_webapps_process_entry_allows_slow_start_before_probe_restart(self) -> None:
        entry = webapps_lib.make_process_entry(
            slug="dashboard",
            command="uv run app",
            cwd="/workspace/webapps/projects/dashboard",
            port=4005,
        )

        self.assertEqual(entry["log_location"], "/workspace/webapps/logs/dashboard.log")
        self.assertEqual(
            entry["log_configuration"],
            {
                "disable_json": True,
                "no_metadata": True,
                "no_color": True,
                "fields_order": ["message"],
                "flush_each_line": True,
            },
        )
        self.assertEqual(
            entry["readiness_probe"],
            {
                "exec": {
                    "command": "bash -c ': <> /dev/tcp/127.0.0.1/4005'",
                },
                "initial_delay_seconds": 5,
                "period_seconds": 10,
                "timeout_seconds": 2,
                "success_threshold": 1,
                "failure_threshold": 6,
            },
        )

    def test_extensionless_webapps_cli_imports_the_split_runtime_helpers(self) -> None:
        self.assertIs(webapps_cli.WEBAPPS_PROJECT, webapps_lib.WEBAPPS_PROJECT)
        self.assertIs(webapps_cli.process_compose_project_update, webapps_lib.process_compose_project_update)
        self.assertIs(webapps_cli.run_process_compose, webapps_lib.run_process_compose)
        self.assertEqual(webapps_cli.DEFAULT_TIMEOUT_SECONDS, 75)

    def test_system_seeder_writes_only_the_system_yaml(self) -> None:
        saved_calls = []

        def save_doc(*, project: object, doc: dict) -> None:
            saved_calls.append((project, doc))

        with tempfile.TemporaryDirectory() as tmpdir:
            argv = [
                "system_process_compose_seed.py",
                "system.gateway",
                "--command",
                "gateway run",
                "--cwd",
                tmpdir,
                "--env",
                "A=B",
            ]
            with (
                patch.object(system_process_compose_seed, "ProcessComposeLock", DummyLock),
                patch.object(
                    system_process_compose_seed,
                    "load_process_compose_yaml",
                    return_value={"version": "0.5", "processes": {}},
                ) as load_yaml,
                patch.object(system_process_compose_seed, "save_process_compose_yaml", side_effect=save_doc) as save_yaml,
                patch.object(system_process_compose_seed.sys, "argv", argv),
            ):
                system_process_compose_seed.main()

        load_yaml.assert_called_once_with(project=webapps_lib.SYSTEM_PROJECT)
        save_yaml.assert_called_once()
        self.assertEqual(len(saved_calls), 1)
        project, doc = saved_calls[0]
        self.assertIs(project, webapps_lib.SYSTEM_PROJECT)
        self.assertEqual(set(doc["processes"]), {"system.gateway"})
        self.assertEqual(doc["processes"]["system.gateway"]["command"], "gateway run")
        self.assertEqual(doc["processes"]["system.gateway"]["log_location"], "/workspace/webapps/logs/system.gateway.log")
        self.assertEqual(
            doc["processes"]["system.gateway"]["log_configuration"],
            {
                "disable_json": True,
                "no_metadata": True,
                "no_color": True,
                "fields_order": ["message"],
                "flush_each_line": True,
            },
        )
        self.assertEqual(doc["processes"]["system.gateway"]["environment"], ["A=B"])


class TestSeedExampleWebapps(unittest.TestCase):
    """First-boot seeding of the bundled example-webapp catalog."""

    def _make_example(
        self,
        root: pathlib.Path,
        slug: str,
        *,
        autostart: bool = True,
        command: str = 'python3 -m http.server "$WEBAPP_PORT" --bind 127.0.0.1',
        files: dict | None = None,
    ) -> pathlib.Path:
        ex_dir = root / slug
        ex_dir.mkdir(parents=True)
        manifest = {
            "slug": slug,
            "title": slug.title(),
            "command": command,
            "autostart": autostart,
        }
        (ex_dir / "example.json").write_text(json.dumps(manifest))
        for name, content in (files or {"index.html": "<h1>hi</h1>"}).items():
            (ex_dir / name).write_text(content)
        return ex_dir

    # --- decide_action (policy comes from the marker) ------------------------

    def test_decide_seed_when_no_marker(self) -> None:
        self.assertEqual(
            seed_example_webapps.decide_action(None, False),
            seed_example_webapps.SEED,
        )

    def test_decide_refresh_overwrites_existing_install(self) -> None:
        self.assertEqual(
            seed_example_webapps.decide_action(seed_example_webapps.POLICY_REFRESH, True),
            seed_example_webapps.REFRESH,
        )

    def test_decide_freeze_never_overwrites(self) -> None:
        self.assertEqual(
            seed_example_webapps.decide_action(seed_example_webapps.POLICY_FREEZE, True),
            seed_example_webapps.SKIP,
        )

    def test_decide_does_not_resurrect_deleted_install(self) -> None:
        # Marker present (was installed) but the user deleted the app — leave it gone.
        self.assertEqual(
            seed_example_webapps.decide_action(seed_example_webapps.POLICY_REFRESH, False),
            seed_example_webapps.SKIP,
        )

    # --- read_policy ---------------------------------------------------------

    def test_read_policy_none_when_no_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(seed_example_webapps, "SEEDED_DIR", pathlib.Path(tmp)):
                self.assertIsNone(seed_example_webapps.read_policy("snake"))

    def test_read_policy_reads_freeze_trimmed_and_lowercased(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = pathlib.Path(tmp)
            (seeded / "snake").write_text("  FREEZE\n")
            with patch.object(seed_example_webapps, "SEEDED_DIR", seeded):
                self.assertEqual(
                    seed_example_webapps.read_policy("snake"),
                    seed_example_webapps.POLICY_FREEZE,
                )

    def test_read_policy_unknown_or_empty_defaults_to_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeded = pathlib.Path(tmp)
            (seeded / "garbage").write_text("frezee")
            (seeded / "blank").write_text("")
            with patch.object(seed_example_webapps, "SEEDED_DIR", seeded):
                self.assertEqual(seed_example_webapps.read_policy("garbage"), seed_example_webapps.POLICY_REFRESH)
                self.assertEqual(seed_example_webapps.read_policy("blank"), seed_example_webapps.POLICY_REFRESH)

    # --- load_manifest -------------------------------------------------------

    def test_load_manifest_parses_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._make_example(root, "snake", autostart=False)
            ex = seed_example_webapps.load_manifest(root / "snake")
        self.assertEqual(ex.slug, "snake")
        self.assertIn("$WEBAPP_PORT", ex.command)
        self.assertFalse(ex.autostart)

    def test_load_manifest_rejects_dir_slug_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ex_dir = pathlib.Path(tmp) / "snake"
            ex_dir.mkdir()
            (ex_dir / "example.json").write_text(json.dumps({"slug": "other", "command": "x"}))
            self.assertIsNone(seed_example_webapps.load_manifest(ex_dir))

    def test_load_manifest_rejects_internal_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ex_dir = pathlib.Path(tmp) / "__admin"
            ex_dir.mkdir()
            (ex_dir / "example.json").write_text(json.dumps({"slug": "__admin", "command": "x"}))
            self.assertIsNone(seed_example_webapps.load_manifest(ex_dir))

    def test_load_manifest_rejects_missing_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ex_dir = pathlib.Path(tmp) / "snake"
            ex_dir.mkdir()
            (ex_dir / "example.json").write_text(json.dumps({"slug": "snake"}))
            self.assertIsNone(seed_example_webapps.load_manifest(ex_dir))

    # --- seed_one ------------------------------------------------------------

    def test_seed_one_first_install_copies_registers_and_marks_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            projects = root / "projects"
            seeded = root / "seeded"
            self._make_example(catalog, "snake")
            ex = seed_example_webapps.load_manifest(catalog / "snake")
            with (
                patch.object(seed_example_webapps, "PROJECTS_DIR", projects),
                patch.object(seed_example_webapps, "SEEDED_DIR", seeded),
                patch.object(seed_example_webapps.subprocess, "run") as run,
            ):
                seed_example_webapps.seed_one(ex)

            self.assertTrue((projects / "snake" / "index.html").is_file())
            # Catalog metadata is not copied into the user's project.
            self.assertFalse((projects / "snake" / "example.json").exists())
            run.assert_called_once()
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[:3], [seed_example_webapps.WEBAPPS_CLI, "create", "snake"])
            self.assertIn("--if-missing", cmd)
            self.assertIn("--bootstrap-enabled", cmd)
            # First install stamps the marker with the default policy.
            self.assertEqual((seeded / "snake").read_text().strip(), seed_example_webapps.POLICY_REFRESH)

    def test_seed_one_refresh_marker_clobbers_user_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            projects = root / "projects"
            (projects / "snake").mkdir(parents=True)
            seeded = root / "seeded"
            seeded.mkdir()
            (projects / "snake" / "user_edit.txt").write_text("mine")
            (seeded / "snake").write_text("refresh\n")
            self._make_example(catalog, "snake", files={"index.html": "<h1>v2</h1>"})
            ex = seed_example_webapps.load_manifest(catalog / "snake")
            with (
                patch.object(seed_example_webapps, "PROJECTS_DIR", projects),
                patch.object(seed_example_webapps, "SEEDED_DIR", seeded),
                patch.object(seed_example_webapps.subprocess, "run") as run,
            ):
                seed_example_webapps.seed_one(ex)

            self.assertFalse((projects / "snake" / "user_edit.txt").exists())
            self.assertEqual((projects / "snake" / "index.html").read_text(), "<h1>v2</h1>")
            run.assert_called_once()

    def test_seed_one_freeze_marker_preserves_user_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            projects = root / "projects"
            (projects / "snake").mkdir(parents=True)
            seeded = root / "seeded"
            seeded.mkdir()
            (projects / "snake" / "user_edit.txt").write_text("mine")
            # The user (or agent) froze this install by writing "freeze" to the marker.
            (seeded / "snake").write_text("freeze\n")
            self._make_example(catalog, "snake", files={"index.html": "<h1>v2</h1>"})
            ex = seed_example_webapps.load_manifest(catalog / "snake")
            with (
                patch.object(seed_example_webapps, "PROJECTS_DIR", projects),
                patch.object(seed_example_webapps, "SEEDED_DIR", seeded),
                patch.object(seed_example_webapps.subprocess, "run") as run,
            ):
                seed_example_webapps.seed_one(ex)

            # Untouched: edit preserved, baked source not copied, nothing registered.
            self.assertTrue((projects / "snake" / "user_edit.txt").exists())
            self.assertFalse((projects / "snake" / "index.html").exists())
            run.assert_not_called()

    # --- main ----------------------------------------------------------------

    def test_main_skips_without_public_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = pathlib.Path(tmp) / "catalog"
            catalog.mkdir()
            self._make_example(catalog, "snake")
            with (
                patch.object(seed_example_webapps, "EXAMPLES_DIR", catalog),
                patch.object(seed_example_webapps.subprocess, "run") as run,
                patch.dict(seed_example_webapps.os.environ, {}, clear=True),
            ):
                seed_example_webapps.main()
        run.assert_not_called()

    def test_main_seeds_catalog_when_routing_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            projects = root / "projects"
            seeded = root / "seeded"
            self._make_example(catalog, "snake")
            with (
                patch.object(seed_example_webapps, "EXAMPLES_DIR", catalog),
                patch.object(seed_example_webapps, "PROJECTS_DIR", projects),
                patch.object(seed_example_webapps, "SEEDED_DIR", seeded),
                patch.object(seed_example_webapps.subprocess, "run") as run,
                patch.dict(seed_example_webapps.os.environ, {"HUMR_PUBLIC_HOSTNAME": "agent.example.com"}),
            ):
                seed_example_webapps.main()

            self.assertTrue((projects / "snake" / "index.html").is_file())
            self.assertTrue((seeded / "snake").is_file())
            run.assert_called_once()

    # --- wiring guards -------------------------------------------------------

    def test_repo_snakes_manifest_is_valid(self) -> None:
        hermes_agent = _humr_runtime_dir().parent
        snakes_dir = hermes_agent / "webapps" / "examples" / "snakes"
        ex = seed_example_webapps.load_manifest(snakes_dir)
        self.assertIsNotNone(ex)
        self.assertEqual(ex.slug, "snakes")
        self.assertIn("$WEBAPP_PORT", ex.command)
        self.assertTrue(ex.autostart)

    def test_webui_seeds_examples_in_main_after_admin_bootstrap(self) -> None:
        script = (_humr_runtime_dir() / "webui.sh").read_text()
        self.assertIn("seed_example_webapps()", script)
        self.assertIn("seed_example_webapps.py", script)
        # Called in main() immediately after the admin bootstrap.
        self.assertRegex(script, r"bootstrap_admin_webapp\n\s+seed_example_webapps\n")

    def test_dockerfile_bakes_example_catalog(self) -> None:
        dockerfile = (_humr_runtime_dir().parent / "Dockerfile").read_text()
        self.assertIn("COPY webapps/examples /opt/humr/webapps/examples", dockerfile)

    def test_nono_profile_allows_seed_script(self) -> None:
        profile = json.loads((_humr_runtime_dir() / "hermes-nono-profile.json").read_text())
        self.assertIn(
            "/opt/humr/runtime/webapps/seed_example_webapps.py",
            profile["filesystem"]["read_file"],
        )
