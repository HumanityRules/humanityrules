"""Tests for the Hermes webapps process-compose runtime contract."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def _runtime_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "template_repos" / "hermes_agent" / "doh_runtime" / "webapps"


def _doh_runtime_dir() -> pathlib.Path:
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
        script = (_doh_runtime_dir() / "webui.sh").read_text()

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
                    "command": "bash -c 'echo > /dev/tcp/127.0.0.1/4005'",
                },
                "initial_delay_seconds": 5,
                "period_seconds": 2,
                "timeout_seconds": 2,
                "success_threshold": 1,
                "failure_threshold": 30,
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
