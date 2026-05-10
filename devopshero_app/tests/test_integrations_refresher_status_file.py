"""Tests for integrations_refresher.py's status-file publication.

The refresher runs inside the customer-env Hermes container (not Django), but
its correctness is load-bearing for the WebUI extension's Integrations pane.
We unit-test the status-file output for each of the 4 user-visible outcomes
(connected / not_connected / revoked / transient_error). The `fatal` branch
calls sys.exit and so doesn't publish — not tested at file level.
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


def _load_refresher_module():
    """Load template_repos/hermes_agent/integrations_refresher.py as a module.

    The script isn't on the Python path by default — it ships with the Hermes
    container image. Loading it by file path lets us exercise its private
    helpers directly without having to stand up the whole container.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    script_path = repo_root / "template_repos" / "hermes_agent" / "integrations_refresher.py"
    spec = importlib.util.spec_from_file_location(
        name="integrations_refresher_under_test",
        location=str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["integrations_refresher_under_test"] = module
    spec.loader.exec_module(module)
    return module


refresher = _load_refresher_module()


class _StatusFileTestBase(unittest.TestCase):

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.token_file = os.path.join(self._tmpdir.name, "google_access_token")
        self.status_file = os.path.join(self._tmpdir.name, "integrations_status.json")
        # Common envelope used across assertions.
        self.control_plane_url = "https://devopshero.ai"
        self.env_slug = "default"
        self.owner_username = "vmendi@example.com"

    def _read_status(self) -> dict:
        with open(self.status_file) as f:
            return json.load(f)


class TestPublishStatusMerges(_StatusFileTestBase):

    def test_first_publish_creates_file_with_envelope(self) -> None:
        refresher._publish_status(
            status_file=self.status_file,
            control_plane_url=self.control_plane_url,
            env_slug=self.env_slug,
            owner_username=self.owner_username,
            provider="google",
            provider_entry={"label": "Google Workspace", "status": "connected", "last_refreshed_at": None},
        )
        data = self._read_status()
        self.assertEqual(data["doh_control_plane_url"], self.control_plane_url)
        self.assertEqual(data["env_slug"], self.env_slug)
        self.assertEqual(data["owner_username"], self.owner_username)
        self.assertIn("google", data["providers"])
        self.assertEqual(data["providers"]["google"]["status"], "connected")

    def test_second_publish_different_provider_preserves_first(self) -> None:
        refresher._publish_status(
            status_file=self.status_file,
            control_plane_url=self.control_plane_url,
            env_slug=self.env_slug,
            owner_username=self.owner_username,
            provider="google",
            provider_entry={"label": "Google Workspace", "status": "connected", "last_refreshed_at": None},
        )
        refresher._publish_status(
            status_file=self.status_file,
            control_plane_url=self.control_plane_url,
            env_slug=self.env_slug,
            owner_username=self.owner_username,
            provider="slack",
            provider_entry={"label": "Slack", "status": "not_connected", "last_refreshed_at": None},
        )
        data = self._read_status()
        self.assertIn("google", data["providers"])
        self.assertIn("slack", data["providers"])


class TestRefreshLoopOneShotPerOutcome(_StatusFileTestBase):
    """Drive _google_refresh_loop through a single iteration for each outcome.

    We break out of the infinite loop by making `time.sleep` raise
    StopIteration — the loop's final call per iteration. The token file and
    status file are asserted post-hoc.
    """

    def _run_one_iteration(self, fetch_result: dict) -> None:
        with patch.object(refresher, "_fetch_google_access_token", return_value=fetch_result), \
             patch.object(refresher.time, "sleep", side_effect=StopIteration), \
             patch.object(refresher.random, "uniform", return_value=0.0):
            try:
                refresher._google_refresh_loop(
                    control_plane_url=self.control_plane_url,
                    bearer="bearer-value",
                    env_slug=self.env_slug,
                    owner_username=self.owner_username,
                    token_file=self.token_file,
                    status_file=self.status_file,
                )
            except StopIteration:
                pass

    def test_ok_outcome_writes_token_and_connected_status(self) -> None:
        self._run_one_iteration(
            fetch_result={"kind": "ok", "access_token": "ya29.fresh", "expires_in": 3600},
        )
        with open(self.token_file) as f:
            self.assertEqual(f.read(), "ya29.fresh")
        entry = self._read_status()["providers"]["google"]
        self.assertEqual(entry["status"], "connected")
        self.assertEqual(entry["label"], "Google Workspace")
        self.assertIsNotNone(entry["last_refreshed_at"])

    def test_not_connected_outcome_removes_token_and_publishes_status(self) -> None:
        # Seed a stale token to confirm the loop removes it.
        with open(self.token_file, "w") as f:
            f.write("stale")
        self._run_one_iteration(fetch_result={"kind": "not_connected"})
        self.assertFalse(os.path.exists(self.token_file))
        entry = self._read_status()["providers"]["google"]
        self.assertEqual(entry["status"], "not_connected")
        self.assertIsNone(entry["last_refreshed_at"])

    def test_revoked_outcome_removes_token_and_publishes_status(self) -> None:
        with open(self.token_file, "w") as f:
            f.write("stale")
        self._run_one_iteration(fetch_result={"kind": "revoked"})
        self.assertFalse(os.path.exists(self.token_file))
        entry = self._read_status()["providers"]["google"]
        self.assertEqual(entry["status"], "revoked")
        self.assertIsNone(entry["last_refreshed_at"])

    def test_transient_outcome_publishes_transient_error_status(self) -> None:
        self._run_one_iteration(
            fetch_result={"kind": "transient", "detail": "network down"},
        )
        entry = self._read_status()["providers"]["google"]
        self.assertEqual(entry["status"], "transient_error")
        self.assertIsNone(entry["last_refreshed_at"])


class TestAtomicWrite(_StatusFileTestBase):

    def test_write_goes_through_tmp_then_rename(self) -> None:
        refresher._write_file_atomically(path=self.status_file, content="{}")
        self.assertTrue(os.path.exists(self.status_file))
        # No .tmp artifact left behind.
        self.assertFalse(os.path.exists(self.status_file + ".tmp"))
