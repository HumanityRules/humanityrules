"""Ingest a Google Cloud Console OAuth 2.0 Client ID JSON file.

Usage:

    uv run manage.py setup_google_oauth_client --file path/to/client.json

The JSON is the file you get from Google Cloud Console:
APIs & Services -> Credentials -> your OAuth 2.0 Client ID -> Download JSON.

We store the `web` object verbatim as the provider config; re-running the
command with a new file updates the row in place (rotation).
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import IntegrationConfig


REQUIRED_WEB_KEYS = ("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")


class Command(BaseCommand):
    help = "Load a Google OAuth 2.0 Client ID JSON (from Google Cloud Console) into IntegrationConfig."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--file",
            required=True,
            help="Path to the OAuth client JSON file downloaded from Google Cloud Console.",
        )

    def handle(self, *args, **options) -> None:
        path = Path(options["file"]).expanduser()
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CommandError(f"Not a valid JSON file: {exc}")

        if "installed" in payload and "web" not in payload:
            raise CommandError(
                "This looks like a 'Desktop app' OAuth client (has `installed` key). "
                "We need a 'Web application' client. In Google Cloud Console, create "
                "an OAuth 2.0 Client ID of type 'Web application' and download again."
            )

        web = payload.get("web")
        if not isinstance(web, dict):
            raise CommandError(
                "Unexpected JSON shape: missing top-level `web` object. "
                "Make sure you downloaded the JSON from a 'Web application' OAuth 2.0 Client ID."
            )

        missing = [k for k in REQUIRED_WEB_KEYS if not web.get(k)]
        if missing:
            raise CommandError(f"`web` object is missing required keys: {', '.join(missing)}")

        redirect_uris = web.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            raise CommandError("`web.redirect_uris` must be a non-empty list.")

        _, created = IntegrationConfig.objects.update_or_create(
            provider=IntegrationConfig.Provider.GOOGLE,
            defaults={"config": web},
        )

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} Google OAuth client config."))
        self.stdout.write(f"  client_id:      {web['client_id']}")
        self.stdout.write(f"  project_id:     {web.get('project_id', '(not set)')}")
        self.stdout.write(f"  auth_uri:       {web['auth_uri']}")
        self.stdout.write(f"  token_uri:      {web['token_uri']}")
        self.stdout.write(f"  redirect_uris:  {', '.join(redirect_uris)}")
