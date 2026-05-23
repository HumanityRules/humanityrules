"""Tests for POST /api/integrations/github/token — env-resident token refresh."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from devopshero_app.models import (
    AWSAccount,
    Environment,
    EnvironmentBearerToken,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    User,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@override_settings(
    GITHUB_APP_CLIENT_ID="test-cid",
    GITHUB_APP_CLIENT_SECRET="test-secret",
)
class _GithubTokenEndpointTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="GH Org", slug="gh-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="GH Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        self.user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.raw_token = "g" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.env, token_hash=_hash(self.raw_token),
        )
        self.integration = IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GITHUB,
            credentials={"refresh_token": "ghr_existing"},
            config={"scope": "repo"},
        )
        self.client = Client()

    def _post(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/integrations/github/token",
            data=json.dumps({"app_slug": "hermes", **body}),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()

    def _patched_github(self, status: int, body: dict):
        http_response = MagicMock()
        http_response.status_code = status
        http_response.json.return_value = body
        return patch(
            "devopshero_app.views.integrations.github_token_refresh.httpx.post",
            return_value=http_response,
        )


class TestAuth(_GithubTokenEndpointTestBase):

    def test_missing_bearer_returns_401(self) -> None:
        status, body = self._post(body={"owner_username": "vmendi"}, token=None)
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_returns_401(self) -> None:
        status, body = self._post(body={"owner_username": "vmendi"}, token="nope")
        self.assertEqual(status, 401)

    def test_invalid_json_returns_400(self) -> None:
        response = self.client.post(
            "/api/integrations/github/token",
            data="{not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_owner_username_returns_400(self) -> None:
        status, body = self._post(body={}, token=self.raw_token)
        self.assertEqual(status, 400)


class TestHappyPath(_GithubTokenEndpointTestBase):

    def test_returns_access_token_on_success(self) -> None:
        with self._patched_github(
            status=200,
            body={
                "access_token": "ghu_fresh",
                "refresh_token": "ghr_rotated",
                "expires_in": 28800,
                "token_type": "bearer",
            },
        ) as post_mock:
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["access_token"], "ghu_fresh")
        self.assertEqual(body["expires_in"], 28800)

        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://github.com/login/oauth/access_token")
        self.assertEqual(kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(kwargs["data"]["refresh_token"], "ghr_existing")
        self.assertEqual(kwargs["data"]["client_id"], "test-cid")
        self.assertEqual(kwargs["data"]["client_secret"], "test-secret")

        self.integration.refresh_from_db()
        self.assertIsNotNone(self.integration.last_refreshed_at)
        # GitHub rotates refresh_token on every refresh — we must persist.
        self.assertEqual(self.integration.credentials["refresh_token"], "ghr_rotated")


class TestConcurrentRefreshRace(_GithubTokenEndpointTestBase):
    """Two near-simultaneous refreshes for the same grant must not corrupt the row."""

    def test_loser_with_bad_refresh_token_does_not_delete_winner_row(self) -> None:
        # The dangerous variant: B holds stale R1. While B is mid-flight at
        # GitHub, A finishes its refresh and persists R2. GitHub then tells
        # B that R1 is bad_refresh_token (because A consumed it). Naïve
        # code would delete the row — but that row now holds A's valid R2.
        # CAS-on-delete: only delete if the row still holds R1.
        from devopshero_app.models import IntegrationUserCredential
        from unittest.mock import MagicMock, patch

        def fake_exchange(*args, **kwargs):
            # A's persist lands while B is still talking to GitHub.
            IntegrationUserCredential.objects.filter(id=self.integration.id).update(
                credentials={"refresh_token": "ghr_winner_R2"},
            )
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "error": "bad_refresh_token",
                "error_description": "The refresh token has been consumed.",
            }
            return response

        with patch(
            "devopshero_app.views.integrations.github_token_refresh.httpx.post",
            side_effect=fake_exchange,
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )

        # B's call returns "stale, retry" 409, NOT 410 ("revoked").
        self.assertEqual(status, 409)
        # A's row survives intact. B's stale revoke didn't delete it.
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.credentials["refresh_token"], "ghr_winner_R2")

    def test_loser_does_not_overwrite_winner(self) -> None:
        # Simulate the race: B reads R1 from the DB. While B's call to
        # GitHub is in flight, A finishes its own refresh and persists R2.
        # B's call returns R3, B then tries to persist R3 — but since the
        # row no longer holds R1 (A wrote R2), B's CAS-on-WHERE update
        # affects 0 rows, leaving R2 intact.
        from devopshero_app.models import IntegrationUserCredential
        from unittest.mock import MagicMock, patch

        def fake_exchange(*args, **kwargs):
            # Simulate "A persists R2 while B is mid-flight at GitHub".
            IntegrationUserCredential.objects.filter(id=self.integration.id).update(
                credentials={"refresh_token": "ghr_winner_R2"},
            )
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "access_token": "ghu_loser_access",
                "refresh_token": "ghr_loser_R3",
                "expires_in": 28800,
                "token_type": "bearer",
            }
            return response

        with patch(
            "devopshero_app.views.integrations.github_token_refresh.httpx.post",
            side_effect=fake_exchange,
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )

        # B's call still returned its access_token (valid for 8h regardless
        # of who's persisted what refresh_token).
        self.assertEqual(status, 200)
        self.assertEqual(body["access_token"], "ghu_loser_access")
        # But B did NOT overwrite A's R2 with its own R3.
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.credentials["refresh_token"], "ghr_winner_R2")


class TestNotConnected(_GithubTokenEndpointTestBase):

    def test_unknown_user_returns_404(self) -> None:
        status, body = self._post(
            body={"owner_username": "not-a-user"}, token=self.raw_token,
        )
        self.assertEqual(status, 404)

    def test_user_without_integration_row_returns_404(self) -> None:
        User.objects.create_user(
            username="alice", password="pw", current_organization=self.org,
        )
        status, body = self._post(
            body={"owner_username": "alice"}, token=self.raw_token,
        )
        self.assertEqual(status, 404)

    def test_user_from_another_org_does_not_satisfy_refresh(self) -> None:
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        other_user = User.objects.create_user(
            username="outsider", password="pw", current_organization=other_org,
        )
        IntegrationUserCredential.objects.create(
            owner_user=other_user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GITHUB,
            credentials={"refresh_token": "ghr_wrong_org"},
            config={"scope": "repo"},
        )

        with self._patched_github(
            status=200,
            body={
                "access_token": "ghu_wrong",
                "refresh_token": "ghr_wrong_rotated",
                "expires_in": 28800,
                "token_type": "bearer",
            },
        ) as post_mock:
            status, body = self._post(
                body={"owner_username": "outsider"}, token=self.raw_token,
            )

        self.assertEqual(status, 404)
        self.assertIn("error", body)
        post_mock.assert_not_called()


class TestRevocation(_GithubTokenEndpointTestBase):

    def test_bad_refresh_token_deletes_row_and_returns_410(self) -> None:
        # GitHub returns HTTP 200 with an error body when the refresh_token
        # is no longer valid — the unusual quirk this endpoint guards.
        with self._patched_github(
            status=200,
            body={"error": "bad_refresh_token", "error_description": "..."},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 410)
        self.assertFalse(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_bad_credentials_also_treated_as_revoked(self) -> None:
        with self._patched_github(
            status=200,
            body={"error": "bad_credentials"},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 410)


class TestTransientFailures(_GithubTokenEndpointTestBase):

    def test_github_5xx_returns_502_and_preserves_row(self) -> None:
        with self._patched_github(
            status=500,
            body={"error": "internal"},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 502)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_unknown_error_returns_502_and_preserves_row(self) -> None:
        with self._patched_github(
            status=200,
            body={"error": "some_new_error_code_we_dont_know"},
        ):
            status, body = self._post(
                body={"owner_username": "vmendi"}, token=self.raw_token,
            )
        self.assertEqual(status, 502)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    @override_settings(GITHUB_APP_CLIENT_ID="", GITHUB_APP_CLIENT_SECRET="")
    def test_settings_missing_returns_500(self) -> None:
        status, body = self._post(
            body={"owner_username": "vmendi"}, token=self.raw_token,
        )
        self.assertEqual(status, 500)
