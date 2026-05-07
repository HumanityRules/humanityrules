"""Tests for /integrations/google/callback/ — the OAuth finish view."""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from devopshero_app.models import (
    AWSAccount,
    Environment,
    IntegrationConfig,
    Organization,
    User,
)
from devopshero_app.tests.test_env_policy_proxy_secrets import (
    FakeSecretsManager,
    _session_with,
)


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["https://devopshero.ai/integrations/google/callback"],
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

    def _seed_session(
        self,
        state: str,
        rd: str,
        env_slug: str,
        username: str,
    ) -> None:
        session = self.client.session
        session["google_oauth_state"] = state
        session["google_oauth_payload"] = {"rd": rd, "env_slug": env_slug, "username": username}
        session.save()

    def _patched_google_and_aws(self, fake: FakeSecretsManager):
        """Context managers to stub the two network-edge calls in the callback."""
        token_response = MagicMock()
        token_response.json.return_value = GOOGLE_TOKEN_RESPONSE
        token_response.raise_for_status.return_value = None

        return (
            patch("devopshero_app.views.integrations_google.httpx.post", return_value=token_response),
            patch(
                "devopshero_app.views.integrations_google.iam_utils.get_assumed_role_session",
                return_value=_session_with(fake),
            ),
        )


class TestIntegrationsGoogleCallbackHappyPath(_CallbackTestBase):

    def test_writes_tokens_to_secrets_manager_and_redirects(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/settings/connections",
            env_slug="staging",
            username="vmendi",
        )
        fake = FakeSecretsManager()
        post_cm, aws_cm = self._patched_google_and_aws(fake)

        with post_cm as post_mock, aws_cm:
            response = self.client.get(
                reverse("integrations_google_callback"),
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
            "https://devopshero.ai/integrations/google/callback",
        )

        # Secret landed at the per-user path with a provider bag.
        secret_name = "devopshero/staging/users/vmendi"
        self.assertIn(secret_name, fake.store)
        stored = json.loads(fake.store[secret_name]["SecretString"])
        google_tokens = stored["google"]
        self.assertEqual(google_tokens["access_token"], "ya29.access")
        self.assertEqual(google_tokens["refresh_token"], "1//refresh")
        self.assertEqual(google_tokens["token_type"], "Bearer")
        # expires_at is an epoch seconds value set to now + expires_in.
        self.assertGreater(google_tokens["expires_at"], google_tokens["granted_at"])

    def test_writing_google_preserves_other_providers_on_same_user(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/x",
            env_slug="staging",
            username="vmendi",
        )
        fake = FakeSecretsManager()
        # Pre-seed a Slack entry under the same user's secret. The callback
        # must leave it alone.
        secret_name = "devopshero/staging/users/vmendi"
        fake.create_secret(
            Name=secret_name,
            Description="seed",
            SecretString=json.dumps({"slack": {"access_token": "xoxb-keep-me"}}),
        )

        post_cm, aws_cm = self._patched_google_and_aws(fake)
        with post_cm, aws_cm:
            self.client.get(
                reverse("integrations_google_callback"),
                {"code": "c", "state": "stst"},
            )

        stored = json.loads(fake.store[secret_name]["SecretString"])
        self.assertEqual(stored["slack"]["access_token"], "xoxb-keep-me")
        self.assertEqual(stored["google"]["access_token"], "ya29.access")

        # Session state popped.
        self.assertNotIn("google_oauth_state", self.client.session)
        self.assertNotIn("google_oauth_payload", self.client.session)


class TestIntegrationsGoogleCallbackRejections(_CallbackTestBase):

    def test_rejects_state_mismatch(self) -> None:
        self._seed_session(state="expected", rd="https://hermes.dev.example.com/x",
                           env_slug="staging", username="vmendi")

        response = self.client.get(
            reverse("integrations_google_callback"),
            {"code": "c", "state": "wrong"},
        )
        self.assertEqual(response.status_code, 400)
        # On rejection we still clear the session to prevent replay.
        self.assertNotIn("google_oauth_state", self.client.session)

    def test_rejects_missing_state_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_google_callback"),
            {"code": "c", "state": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_when_google_returns_error(self) -> None:
        response = self.client.get(
            reverse("integrations_google_callback"),
            {"error": "access_denied"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("access_denied", response.content.decode())

    def test_rejects_when_authenticated_user_mismatches_session_payload(self) -> None:
        # Session was started by "vmendi" but another user is authenticated when
        # the callback fires — refuse to write tokens under the wrong key.
        other = User.objects.create_user(
            username="someone-else",
            email="se@example.com",
            password="pw",
            current_organization=self.org,
        )
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_slug="staging", username="vmendi")

        self.client.force_login(other)
        # force_login cycles the session, so re-seed post-login.
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_slug="staging", username="vmendi")

        response = self.client.get(
            reverse("integrations_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_when_env_missing(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_slug="nonexistent", username="vmendi")

        response = self.client.get(
            reverse("integrations_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)

    def test_token_exchange_failure_returns_400_and_no_secret_written(self) -> None:
        self._seed_session(state="stst", rd="https://hermes.dev.example.com/x",
                           env_slug="staging", username="vmendi")
        fake = FakeSecretsManager()

        with patch(
            "devopshero_app.views.integrations_google.httpx.post",
            side_effect=RuntimeError("boom"),
        ), patch(
            "devopshero_app.views.integrations_google.iam_utils.get_assumed_role_session",
            return_value=_session_with(fake),
        ):
            response = self.client.get(
                reverse("integrations_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.store, {})


class TestIntegrationsGoogleCallbackRdAppend(_CallbackTestBase):

    def test_appends_connected_param_to_rd_with_existing_query(self) -> None:
        self._seed_session(
            state="stst",
            rd="https://hermes.dev.example.com/x?foo=bar",
            env_slug="staging",
            username="vmendi",
        )
        fake = FakeSecretsManager()
        post_cm, aws_cm = self._patched_google_and_aws(fake)

        with post_cm, aws_cm:
            response = self.client.get(
                reverse("integrations_google_callback"),
                {"code": "c", "state": "stst"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("foo=bar", response["Location"])
        self.assertIn("connected=google", response["Location"])
