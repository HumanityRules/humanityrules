"""Store DOH's X (Twitter) OAuth 2.0 client credentials in IntegrationConfig.

Usage:

    uv run manage.py setup_x_oauth_client \
        --client-id YOUR_CLIENT_ID \
        --client-secret YOUR_CLIENT_SECRET \
        --redirect-uri https://humanityrules.io/integrations/user/x/callback \
        --redirect-uri https://<ngrok-host>/integrations/user/x/callback

Unlike Google (which hands you a downloadable JSON), X gives you a bare Client ID
and Client Secret in the developer console under your app's "User authentication
settings" (OAuth 2.0, app type "Web App, Automated App or Bot"). X's authorize
and token endpoints are fixed, so we synthesize the same `config` shape
`provider_x` reads — matching Google's stored `web` object — from these flags.

The redirect URIs must byte-match the callback URLs registered in the X console;
pass one `--redirect-uri` per host DOH is reachable at (prod + ngrok for dev).

Re-running with new values updates the row in place (rotation).
"""

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import IntegrationConfig


# X's OAuth 2.0 endpoints are constant for every app (docs.x.com), so they're
# baked in here rather than collected — but stored in `config` so `provider_x`
# reads client_id/client_secret/auth_uri/token_uri/redirect_uris uniformly.
X_AUTH_URI = "https://x.com/i/oauth2/authorize"
X_TOKEN_URI = "https://api.x.com/2/oauth2/token"


class Command(BaseCommand):
    help = "Load DOH's X OAuth 2.0 client (Client ID + Secret) into IntegrationConfig."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--client-id", required=True, help="OAuth 2.0 Client ID from the X developer console.")
        parser.add_argument("--client-secret", required=True, help="OAuth 2.0 Client Secret from the X developer console.")
        parser.add_argument(
            "--redirect-uri",
            required=True,
            action="append",
            dest="redirect_uris",
            help="Callback URL registered in the X console (exact match). Repeat for each host (prod, ngrok).",
        )

    def handle(self, *args, **options) -> None:
        client_id = options["client_id"].strip()
        client_secret = options["client_secret"].strip()
        redirect_uris = [uri.strip() for uri in options["redirect_uris"] if uri.strip()]

        if not client_id:
            raise CommandError("--client-id must not be empty.")
        if not client_secret:
            raise CommandError("--client-secret must not be empty.")
        if not redirect_uris:
            raise CommandError("At least one non-empty --redirect-uri is required.")

        config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": X_AUTH_URI,
            "token_uri": X_TOKEN_URI,
            "redirect_uris": redirect_uris,
        }

        _, created = IntegrationConfig.objects.update_or_create(
            provider=IntegrationConfig.Provider.X,
            defaults={"config": config},
        )

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} X OAuth client config."))
        self.stdout.write(f"  client_id:      {client_id}")
        self.stdout.write(f"  auth_uri:       {X_AUTH_URI}")
        self.stdout.write(f"  token_uri:      {X_TOKEN_URI}")
        self.stdout.write(f"  redirect_uris:  {', '.join(redirect_uris)}")
