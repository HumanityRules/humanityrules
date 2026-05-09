"""Tests for /integrations/google/start/ — the OAuth kickoff view."""

from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse

from devopshero_app.models import (
    AWSAccount,
    Environment,
    IntegrationConfig,
    Organization,
    User,
)


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["https://devopshero.ai/integrations/google/callback"],
}


class TestIntegrationsGoogleStart(TestCase):

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

        self.google_config = IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )

    def test_login_required_when_unauthenticated(self) -> None:
        self.client.logout()
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://hermes.dev.example.com/x"},
        )
        self.assertEqual(response.status_code, 302)
        # The global LOGIN_URL points at WorkOS — we don't care about the destination,
        # just that the decorator kicked in.
        self.assertNotIn("accounts.google.com", response["Location"])

    def test_redirects_to_google_with_expected_params(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://hermes.dev.example.com/settings/connections"},
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/auth")

        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], ["cid-123.apps.googleusercontent.com"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["redirect_uri"], ["https://devopshero.ai/integrations/google/callback"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", params["scope"][0])
        self.assertIn("openid", params["scope"][0])

    def test_stashes_state_and_payload_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://hermes.dev.example.com/x"},
        )
        parsed = urlparse(response["Location"])
        state = parse_qs(parsed.query)["state"][0]

        session = self.client.session
        self.assertEqual(session["google_oauth_state"], state)
        payload = session["google_oauth_payload"]
        self.assertEqual(payload["rd"], "https://hermes.dev.example.com/x")
        self.assertEqual(payload["env_slug"], "staging")
        self.assertEqual(payload["owner_username"], "vmendi")

    def test_exact_zone_host_matches(self) -> None:
        # rd host == env zone (no subdomain) should still match.
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://dev.example.com/x"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_rejects_rd_with_unknown_host(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://hermes.other-domain.com/x"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("google_oauth_state", self.client.session)

    def test_rejects_rd_with_suffix_lookalike(self) -> None:
        # "evil-dev.example.com" ends with the zone string "dev.example.com"
        # but is not a subdomain — must be rejected.
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://evil-dev.example.com/x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_http_scheme(self) -> None:
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "javascript:alert(1)"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_missing_rd(self) -> None:
        response = self.client.get(reverse("integrations_google_oauth_start"))
        self.assertEqual(response.status_code, 400)

    def test_errors_when_google_integration_not_configured(self) -> None:
        self.google_config.delete()
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://hermes.dev.example.com/x"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("setup_google_oauth_client", response.content.decode())

    def test_ignores_envs_with_blank_hosted_zone(self) -> None:
        # A second env exists but has no hosted zone — it must not match any rd.
        Environment.objects.create(
            aws_account=self.aws_account,
            name="Dev",
            slug="dev",
            aws_region="us-east-1",
            shared_alb_hosted_zone="",
        )
        response = self.client.get(
            reverse("integrations_google_oauth_start"),
            {"rd": "https://anything.com/x"},
        )
        self.assertEqual(response.status_code, 400)
