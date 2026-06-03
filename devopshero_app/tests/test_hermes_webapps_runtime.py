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
    return repo_root / "template_repos" / "hermes_agent" / "doh_runtime"


def _load_runtime_module(name: str, filename: str) -> types.ModuleType:
    runtime_dir = _runtime_dir()
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))

    script_path = runtime_dir / filename
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

    def test_extensionless_webapps_cli_imports_the_split_runtime_helpers(self) -> None:
        self.assertIs(webapps_cli.WEBAPPS_PROJECT, webapps_lib.WEBAPPS_PROJECT)
        self.assertIs(webapps_cli.process_compose_project_update, webapps_lib.process_compose_project_update)
        self.assertIs(webapps_cli.run_process_compose, webapps_lib.run_process_compose)

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
        self.assertEqual(doc["processes"]["system.gateway"]["environment"], ["A=B"])
