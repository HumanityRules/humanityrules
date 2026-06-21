"""Tests for the `setup_google_oauth_client` management command."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from humanityrules_app.models import IntegrationConfig


VALID_WEB_PAYLOAD = {
    "web": {
        "client_id": "cid-123.apps.googleusercontent.com",
        "project_id": "doh-hermes",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": "csecret",
        "redirect_uris": ["https://humanityrules.io/integrations/user/google/callback/"],
    }
}


class TestSetupGoogleOauthClient(TestCase):

    def _write(self, tmp_path, payload) -> str:
        file_path = tmp_path / "client.json"
        file_path.write_text(json.dumps(payload))
        return str(file_path)

    def test_creates_integration_config_from_valid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), VALID_WEB_PAYLOAD)
            out = StringIO()
            call_command("setup_google_oauth_client", file=path, stdout=out)

        row = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
        self.assertEqual(row.config["client_id"], "cid-123.apps.googleusercontent.com")
        self.assertEqual(row.config["client_secret"], "csecret")
        self.assertIn("https://humanityrules.io/integrations/user/google/callback/", row.config["redirect_uris"])

    def test_rerunning_updates_in_place(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), VALID_WEB_PAYLOAD)
            call_command("setup_google_oauth_client", file=path, stdout=StringIO())

            rotated = json.loads(json.dumps(VALID_WEB_PAYLOAD))
            rotated["web"]["client_secret"] = "new-secret"
            (Path(tmp) / "client.json").write_text(json.dumps(rotated))
            call_command("setup_google_oauth_client", file=path, stdout=StringIO())

        rows = IntegrationConfig.objects.filter(provider=IntegrationConfig.Provider.GOOGLE)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().config["client_secret"], "new-secret")

    def test_rejects_installed_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"installed": {"client_id": "x"}})
            with self.assertRaises(CommandError) as cm:
                call_command("setup_google_oauth_client", file=path, stdout=StringIO())
        self.assertIn("Web application", str(cm.exception))

    def test_rejects_missing_web_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"something": "else"})
            with self.assertRaises(CommandError) as cm:
                call_command("setup_google_oauth_client", file=path, stdout=StringIO())
        self.assertIn("web", str(cm.exception))

    def test_rejects_missing_required_keys(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        incomplete = {"web": {"client_id": "x", "redirect_uris": ["https://doh/cb"]}}
        with TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), incomplete)
            with self.assertRaises(CommandError) as cm:
                call_command("setup_google_oauth_client", file=path, stdout=StringIO())
        self.assertIn("missing required keys", str(cm.exception))

    def test_rejects_invalid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            path.write_text("not json {")
            with self.assertRaises(CommandError) as cm:
                call_command("setup_google_oauth_client", file=str(path), stdout=StringIO())
        self.assertIn("valid JSON", str(cm.exception))

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(CommandError) as cm:
            call_command("setup_google_oauth_client", file="/nonexistent/path.json", stdout=StringIO())
        self.assertIn("not found", str(cm.exception).lower())
