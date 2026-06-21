"""Tests for /integrations/user/x/callback/ — persists refresh_token on DOH.

Mirrors the Google callback tests, but covers the two X-specific differences:
PKCE (the session carries a `code_verifier` that must reach the token exchange)
and confidential-client HTTP Basic auth at the token endpoint.
"""

import base64
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from humanityrules_app.models import (
    AWSAccount,
    Environment,
    IntegrationConfig,
    IntegrationUserCredential,
    Organization,
    User,
)


VALID_X_CONFIG = {
    "client_id": "QzRBSXg5SUYyUzdrWjdkVWVRUHA6MTpjaQ",
    "client_secret": "x-client-secret",
    "auth_uri": "https://x.com/i/oauth2/authorize",
    "token_uri": "https://api.x.com/2/oauth2/token",
    "redirect_uris": [
        "http://testserver/integrations/user/x/callback/",
        "https://humanityrules.ngrok.io/integrations/user/x/callback/",
    ],
}

X_TOKEN_RESPONSE = {
    "token_type": "bearer",
    "expires_in": 7200,
    "access_token": "x-access-token",
    "refresh_token": "x-refresh-token",
    "scope": "tweet.read tweet.write users.read offline.access",
}


class _CallbackTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="NextOrg",
            slug="nextorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org,
            name="Prod",
            aws_account_id="111122223333",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            shared_alb_hosted_zone="dev.example.com",
        )
        self.user = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        self.client.force_login(self.user)

        IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.X,
            config=VALID_X_CONFIG,
        )

    def _seed_session(self, state: str, rd: str, env_id: str, app_slug: str, owner_username: str) -> None:
        session = self.client.session
        session["x_oauth_state"] = state
        session["x_oauth_code_verifier"] = "the-code-verifier"
        session["x_oauth_payload"] = {
            "rd": rd,
            "env_id": env_id,
            "app_slug": app_slug,
            "owner_username": owner_username,
        }
        session.save()

    def _patched_x(self, body: dict | None):
        """Context manager that stubs X's token-exchange POST."""
        token_response = MagicMock()
        token_response.json.return_value = body if body is not None else X_TOKEN_RESPONSE
        token_response.raise_for_status.return_value = None
        return patch(
            "humanityrules_app.views.integrations.provider_x.httpx.post",
            return_value=token_response,
        )


class TestIntegrationsXCallbackHappyPath(_CallbackTestBase):

    def test_persists_row_and_uses_basic_auth_and_pkce(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/settings/connections",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_x(body=None) as post_mock:
            response = self.client.get(
                reverse("integrations_user_x_callback"),
                {"code": "auth-code", "state": "stst"},
            )

        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://api.x.com/2/oauth2/token")
        # Confidential client: Basic auth header, client_id NOT in the body.
        expected_basic = "Basic " + base64.b64encode(
            f"{VALID_X_CONFIG['client_id']}:{VALID_X_CONFIG['client_secret']}".encode()
        ).decode()
        self.assertEqual(kwargs["headers"]["Authorization"], expected_basic)
        self.assertNotIn("client_id", kwargs["data"])
        # PKCE verifier from the session reaches the exchange.
        self.assertEqual(kwargs["data"]["code_verifier"], "the-code-verifier")
        self.assertEqual(kwargs["data"]["code"], "auth-code")
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(
            kwargs["data"]["redirect_uri"],
            "http://testserver/integrations/user/x/callback/",
        )

        row = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.X,
        )
        self.assertEqual(row.credentials["refresh_token"], "x-refresh-token")
        self.assertIn("tweet.write", row.config["scope"])
        self.assertIsNone(row.last_refreshed_at)

        self.assertEqual(response.status_code, 302)
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertIn("connected=x", response["Location"])
        self.assertNotIn("x_oauth_state", self.client.session)
        self.assertNotIn("x_oauth_code_verifier", self.client.session)
        self.assertNotIn("x_oauth_payload", self.client.session)

    def test_reconnect_updates_row_in_place(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.X,
            credentials={"refresh_token": "old-refresh"},
            config={"scope": "stale-scope"},
        )
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/x",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_x(body=None):
            self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "stst"})

        rows = IntegrationUserCredential.objects.filter(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.X,
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().credentials["refresh_token"], "x-refresh-token")


class TestIntegrationsXCallbackRejections(_CallbackTestBase):

    def test_rejects_state_mismatch(self) -> None:
        self._seed_session(state="expected", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        response = self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_missing_code_verifier_in_session(self) -> None:
        session = self.client.session
        session["x_oauth_state"] = "stst"
        session["x_oauth_payload"] = {
            "rd": "https://hermes.dev.example.com/x", "env_id": str(self.env.id),
            "app_slug": "hermes", "owner_username": "vmendi",
        }
        session.save()  # no x_oauth_code_verifier
        response = self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "stst"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_when_x_returns_error(self) -> None:
        response = self.client.get(reverse("integrations_user_x_callback"), {"error": "access_denied"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("access_denied", response.content.decode())

    def test_rejects_when_authenticated_user_mismatches_session_payload(self) -> None:
        other = User.objects.create_user(
            username="someone-else", email="se@example.com", password="pw", current_organization=self.org,
        )
        self.client.force_login(other)
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        response = self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "stst"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_token_exchange_failure_returns_400_and_no_row_written(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        with patch("humanityrules_app.views.integrations.provider_x.httpx.post", side_effect=RuntimeError("boom")):
            response = self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "stst"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_when_x_returns_no_refresh_token(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        body_without_refresh = {
            "token_type": "bearer", "expires_in": 7200,
            "access_token": "x-access-token", "scope": "tweet.read",
        }
        with self._patched_x(body=body_without_refresh):
            response = self.client.get(reverse("integrations_user_x_callback"), {"code": "c", "state": "stst"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())
