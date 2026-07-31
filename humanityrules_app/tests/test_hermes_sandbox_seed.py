"""Tests for the Hermes sandbox boot-seeding contract."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest


def _hermes_agent_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "template_repos" / "hermes_agent"


def _load_sandbox_seed_module() -> types.ModuleType:
    script_path = _hermes_agent_dir() / "humr_runtime" / "sandbox_seed.py"
    spec = importlib.util.spec_from_file_location(name="sandbox_seed_under_test", location=str(script_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["sandbox_seed_under_test"] = module
    spec.loader.exec_module(module)
    return module


sandbox_seed = _load_sandbox_seed_module()


class TestHermesSandboxSeed(unittest.TestCase):

    def test_humr_bundle_refresh_preserves_gallery_install_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text(
                json.dumps(
                    {
                        "version": "2.0.0",
                        "extensions": [
                            {
                                "id": "humr-integrations",
                                "scripts": ["humr-integrations.js"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (source / "humr-integrations.js").write_text("console.log('humr');", encoding="utf-8")

            state_dir = root / "state"
            target = state_dir / "extensions" / "humr"
            target.mkdir(parents=True)
            (target / "stale.js").write_text("stale", encoding="utf-8")
            install_manifest_path = state_dir / "extension-install-manifest.json"
            install_manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "installed": {
                            "humr": {
                                "version": "1.0.0",
                                "files": ["stale.js"],
                                "installed_at": "2026-01-01T00:00:00+00:00",
                            },
                            "skin-pack": {
                                "version": "0.1.0",
                                "files": ["manifest.json", "skin-pack.js"],
                                "installed_at": "2026-02-01T00:00:00+00:00",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            sandbox_seed.seed_humr_webui_extensions(source_dir=source, webui_state_dir=state_dir)

            self.assertFalse((target / "stale.js").exists())
            self.assertTrue((target / "manifest.json").is_file())
            self.assertTrue((target / "humr-integrations.js").is_file())
            install_manifest = json.loads(install_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(install_manifest["installed"]["skin-pack"]["version"], "0.1.0")
            self.assertEqual(install_manifest["installed"]["humr"]["version"], "2.0.0")
            self.assertEqual(
                install_manifest["installed"]["humr"]["installed_at"],
                "2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(
                install_manifest["installed"]["humr"]["files"],
                ["humr-integrations.js", "manifest.json"],
            )

    def test_repo_bundle_manifest_declares_all_humr_extensions(self) -> None:
        manifest_path = _hermes_agent_dir() / "webui-extension" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        extensions = {entry["id"]: entry for entry in manifest["extensions"]}

        self.assertEqual(
            set(extensions),
            {"humr-integrations", "humr-widgets", "humr-webapps", "humr-permissions", "humr-credits"},
        )
        extension_order = [entry["id"] for entry in manifest["extensions"]]
        widgets_index = extension_order.index("humr-widgets")
        self.assertEqual(extension_order[widgets_index + 1], "humr-webapps")
        self.assertEqual(
            extensions["humr-integrations"]["scripts"],
            [
                "humr-panel.js",
                "humr-integrations-runtime.js",
                "humr-integrations-connection-flows.js",
                "humr-integrations-google.js",
                "humr-integrations-slack.js",
                "humr-integrations-merge.js",
                "humr-integrations.js",
            ],
        )
        self.assertIn("humr-model-picker.css", extensions["humr-integrations"]["stylesheets"])
        self.assertEqual(extensions["humr-widgets"]["scripts"], ["humr-panel.js", "humr-widgets.js"])
        self.assertEqual(extensions["humr-widgets"]["stylesheets"], ["humr-widgets.css"])
        self.assertEqual(extensions["humr-webapps"]["scripts"], ["humr-panel.js", "humr-webapps.js"])
        self.assertEqual(extensions["humr-permissions"]["scripts"], ["humr-permissions.js"])
        self.assertEqual(extensions["humr-credits"]["scripts"], ["humr-credits.js"])
        self.assertEqual(extensions["humr-credits"]["stylesheets"], ["humr-credits.css"])

    def test_runtime_uses_webui_managed_extension_root(self) -> None:
        dockerfile = (_hermes_agent_dir() / "Dockerfile").read_text(encoding="utf-8")
        webui_script = (_hermes_agent_dir() / "humr_runtime" / "webui.sh").read_text(encoding="utf-8")
        supervisor_script = (_hermes_agent_dir() / "humr_runtime" / "supervisor.sh").read_text(encoding="utf-8")

        self.assertNotIn("ENV HERMES_WEBUI_EXTENSION_DIR=", dockerfile)
        self.assertNotIn("HERMES_WEBUI_EXTENSION_SCRIPT_URLS", webui_script)
        self.assertNotIn("HERMES_WEBUI_EXTENSION_STYLESHEET_URLS", webui_script)
        self.assertNotIn("HERMES_WEBUI_EXTENSION_DIR must be set", webui_script)
        self.assertNotIn("HERMES_WEBUI_EXTENSION_DIR must be set", supervisor_script)
