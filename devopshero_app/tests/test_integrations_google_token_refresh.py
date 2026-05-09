"""Tests for POST /api/integrations/google/token — env-resident token refresh."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

from devopshero_app.models import (
    AWSAccount,
    Environment,
    EnvironmentBearerToken,
    IntegrationConfig,
    Organization,
    User,
    UserThirdPartyIntegration,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["https://devopshero.ai/integrations/google/callback"],
}


class _TokenEndpointTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Tok Org", slug="tok-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Tok Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        self.user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        self.raw_token = "t" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.env, token_hash=_hash(self.raw_token),
        )
        IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )
        self.integration = UserThirdPartyIntegration.objects.create(
            user=self.user, environment=self.env,
            provider=UserThirdPartyIntegration.Provider.GOOGLE,
            refresh_token="existing-refresh", scope="gmail.readonly openid email",
        )
        self.client = Client()

    def _post(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/integrations/google/token",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()

    def _patched_google(self, status: int, body: dict):
        http_response = MagicMock()
        http_response.status_code = status
        http_response.json.return_value = body
        return patch(
            "devopshero_app.views.integrations.google_token_refresh.httpx.post",
            return_value=http_response,
        )


class TestAuth(_TokenEndpointTestBase):

    def test_missing_bearer_returns_401(self) -> None:
        status, body = self._post(body={"owner_username": "vmendi"}, token=None)
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_returns_401(self) -> None:
        status, body = self._post(body={"owner_username": "vmendi"}, token="nope")
        self.assertEqual(status, 401)

    def test_invalid_json_returns_400(self) -> None:
        response = self.client.post(
            "/api/integrations/google/token",
            data="{not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_owner_username_returns_400(self) -> None:
        status, body = self._post(body={}, token=self.raw_token)
        self.assertEqual(status, 400)


class TestHappyPath(_TokenEndpointTestBase):

    def test_returns_access_token_on_success(self) -> None:
        with self._patched_google(
            status=200,
            body={"access_token": "ya29.fresh", "expires_in": 3599, "token_type": "Bearer"},
        ) as post_mock:
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["access_token"], "ya29.fresh")
        self.assertEqual(body["expires_in"], 3599)
        self.assertEqual(body["token_type"], "Bearer")

        # Outgoing request shape.
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://oauth2.googleapis.com/token")
        self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(kwargs["data"]["refresh_token"], "existing-refresh")
        self.assertEqual(kwargs["data"]["client_id"], "cid-123.apps.googleusercontent.com")

        # last_refreshed_at stamped.
        self.integration.refresh_from_db()
        self.assertIsNotNone(self.integration.last_refreshed_at)
        # refresh_token unchanged (Google didn't rotate it).
        self.assertEqual(self.integration.refresh_token, "existing-refresh")

    def test_rotates_refresh_token_when_google_returns_one(self) -> None:
        with self._patched_google(
            status=200,
            body={
                "access_token": "ya29.fresh",
                "refresh_token": "new-refresh",
                "expires_in": 3599,
                "token_type": "Bearer",
            },
        ):
            self._post(body={"owner_username": "vmendi"}, token=self.raw_token)

        self.integration.refresh_from_db()
        self.assertEqual(self.integration.refresh_token, "new-refresh")


class TestNotConnected(_TokenEndpointTestBase):

    def test_unknown_user_returns_404(self) -> None:
        status, body = self._post(
            body={"owner_username": "not-a-user"}, token=self.raw_token,
        )
        self.assertEqual(status, 404)

    def test_user_without_integration_row_returns_404(self) -> None:
        """User exists in the org but hasn't connected Google in this env."""
        User.objects.create_user(
            username="alice", password="pw", current_organization=self.org,
        )
        status, body = self._post(
            body={"owner_username": "alice"}, token=self.raw_token,
        )
        self.assertEqual(status, 404)


class TestRevocation(_TokenEndpointTestBase):

    def test_google_invalid_grant_deletes_row_and_returns_410(self) -> None:
        with self._patched_google(
            status=400,
            body={"error": "invalid_grant"},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 410)
        self.assertFalse(
            UserThirdPartyIntegration.objects.filter(id=self.integration.id).exists()
        )


class TestTransientFailures(_TokenEndpointTestBase):

    def test_google_5xx_returns_502_and_preserves_row(self) -> None:
        with self._patched_google(
            status=500,
            body={"error": "internal"},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 502)
        # Row preserved — user hasn't revoked, DOH is just asking again later.
        self.assertTrue(
            UserThirdPartyIntegration.objects.filter(id=self.integration.id).exists()
        )

    def test_integration_config_missing_returns_500(self) -> None:
        IntegrationConfig.objects.filter(provider="google").delete()
        status, body = self._post(
            body={"owner_username": "vmendi"}, token=self.raw_token,
        )
        self.assertEqual(status, 500)
