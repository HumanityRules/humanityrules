"""Tests for /integrations/google/callback/ — persists refresh_token on DOH."""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from devopshero_app.models import (
    AWSAccount,
    Environment,
    IntegrationConfig,
    IntegrationUserCredential,
    Organization,
    User,
)


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": [
        "http://testserver/integrations/google/callback",
        "https://devopshero.ai/integrations/google/callback",
    ],
}

GOOGLE_TOKEN_RESPONSE = {
    "access_token": "ya29.access",
    "refresh_token": "1//refresh",
    "scope": "https://www.googleapis.com/auth/gmail.readonly openid email",
    "token_type": "Bearer",
    "expires_in": 3599,
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
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )

    def _seed_session(self, state: str, rd: str, env_id: str, app_slug: str, owner_username: str) -> None:
        session = self.client.session
        session["google_oauth_state"] = state
        session["google_oauth_payload"] = {
            "rd": rd,
            "env_id": env_id,
            "app_slug": app_slug,
            "owner_username": owner_username,
        }
        session.save()

    def _patched_google(self, body: dict | None):
        """Context manager that stubs Google's token-exchange POST."""
        token_response = MagicMock()
        token_response.json.return_value = body if body is not None else GOOGLE_TOKEN_RESPONSE
        token_response.raise_for_status.return_value = None
        return patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post",
            return_value=token_response,
        )


class TestIntegrationsGoogleCallbackHappyPath(_CallbackTestBase):

    def test_persists_row_on_doh_and_redirects(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/settings/connections",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_google(body=None) as post_mock:
            response = self.client.get(
                reverse("integrations_google_oauth_callback"),
                {"code": "auth-code", "state": "stst"},
            )

        # Google token exchange happened with expected shape.
        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://oauth2.googleapis.com/token")
        self.assertEqual(kwargs["data"]["code"], "auth-code")
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(
            kwargs["data"]["redirect_uri"],
            "http://testserver/integrations/google/callback",
        )

        # One IntegrationUserCredential row, keyed by (owner, env, app_slug, provider).
        row = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
        )
        self.assertEqual(row.credentials["refresh_token"], "1//refresh")
        self.assertIn("gmail.readonly", row.config["scope"])
        self.assertIsNone(row.last_refreshed_at)

        # Redirected back to rd with ?connected=google appended, session popped.
        self.assertEqual(response.status_code, 302)
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertIn("connected=google", response["Location"])
        self.assertNotIn("google_oauth_state", self.client.session)
        self.assertNotIn("google_oauth_payload", self.client.session)

    def test_reconnect_updates_row_in_place(self) -> None:
        """Running through consent again should update the existing row, not duplicate."""
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
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

        with self._patched_google(body=None):
            self.client.get(
                reverse("integrations_google_oauth_callback"),
                {"code": "c", "state": "stst"},
            )

        rows = IntegrationUserCredential.objects.filter(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().credentials["refresh_token"], "1//refresh")
        self.assertIn("gmail.readonly", rows.first().config["scope"])


class TestIntegrationsGoogleCallbackRejections(_CallbackTestBase):

    def test_rejects_state_mismatch(self) -> None:
        self._seed_session(state="expected", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_google_oauth_callback"),
            {"code": "c", "state": "wrong"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("google_oauth_state", self.client.session)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_missing_state_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_callback"),
            {"code": "c", "state": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_when_google_returns_error(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_callback"),
            {"error": "access_denied"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("access_denied", response.content.decode())

    def test_rejects_when_authenticated_user_mismatches_session_payload(self) -> None:
        other = User.objects.create_user(
            username="someone-else",
            email="se@example.com",
            password="pw",
            current_organization=self.org,
        )
        self.client.force_login(other)
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_google_oauth_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_when_env_missing(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id="00000000-0000-0000-0000-000000000000", app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_google_oauth_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)

    def test_token_exchange_failure_returns_400_and_no_row_written(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(
                reverse("integrations_google_oauth_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_rejects_when_google_returns_no_refresh_token(self) -> None:
        """Without a refresh_token in the response, DOH can't serve access tokens later."""
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        body_without_refresh = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
        }

        with self._patched_google(body=body_without_refresh):
            response = self.client.get(
                reverse("integrations_google_oauth_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())


class TestIntegrationsGoogleCallbackRdAppend(_CallbackTestBase):

    def test_appends_connected_param_to_rd_with_existing_query(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/x?foo=bar",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_google_oauth_callback"),
                {"code": "c", "state": "stst"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("foo=bar", response["Location"])
        self.assertIn("connected=google", response["Location"])
