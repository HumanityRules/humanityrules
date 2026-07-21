"""Tests for /integrations/user/x/start/ — the OAuth kickoff view (PKCE)."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationConfig,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
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


class TestIntegrationsXStart(TestCase):

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
            organization=self.org, name="Prod", aws_account_id="111122223333",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging",
            aws_region="us-east-1", shared_alb_hosted_zone="dev.example.com",
        )
        self.user = User.objects.create_user(
            username="vmendi", email="vmendi@example.com", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.client.force_login(self.user)
        self.repository = Repository.objects.create(
            organization=self.org, provider="github", name="hermes",
            full_name="org/hermes", default_branch="main",
            clone_url="https://github.com/org/hermes.git",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org, name="Engineering", slug="engineering",
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repository,
            environment=self.env, name="Hermes", slug="hermes",
            build_strategy=App.BuildStrategy.DOCKERFILE,
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type=ResourceTag.ResourceType.APP,
            app=self.app, key="owner", value=self.user.username,
        )
        self.x_config = IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.X, config=VALID_X_CONFIG,
        )

    def _params(self, rd: str) -> dict:
        return {"rd": rd, "app_slug": self.app.slug}

    def test_redirects_to_x_with_pkce_params(self) -> None:
        response = self.client.get(
            reverse("integrations_user_x_start"),
            self._params(rd="https://hermes.dev.example.com/settings/connections"),
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "x.com")
        self.assertEqual(parsed.path, "/i/oauth2/authorize")

        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], [VALID_X_CONFIG["client_id"]])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["redirect_uri"], ["http://testserver/integrations/user/x/callback/"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("offline.access", params["scope"][0])
        self.assertIn("tweet.write", params["scope"][0])

        # code_challenge must be the S256 of the verifier stashed in session.
        verifier = self.client.session["x_oauth_code_verifier"]
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [expected_challenge])

    def test_stashes_state_verifier_and_payload_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_user_x_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        parsed = urlparse(response["Location"])
        state = parse_qs(parsed.query)["state"][0]

        session = self.client.session
        self.assertEqual(session["x_oauth_state"], state)
        self.assertTrue(session["x_oauth_code_verifier"])
        payload = session["x_oauth_payload"]
        self.assertEqual(payload["rd"], "https://hermes.dev.example.com/x")
        self.assertEqual(payload["env_id"], str(self.env.id))
        self.assertEqual(payload["app_slug"], "hermes")
        self.assertEqual(payload["owner_username"], "vmendi")

    def test_errors_when_x_integration_not_configured(self) -> None:
        self.x_config.delete()
        response = self.client.get(
            reverse("integrations_user_x_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("setup_x_oauth_client", response.content.decode())

    def test_rejects_rd_with_unknown_host(self) -> None:
        response = self.client.get(
            reverse("integrations_user_x_start"),
            self._params(rd="https://hermes.other-domain.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("x_oauth_state", self.client.session)
