"""Tests for /integrations/user/google/start/ — the OAuth kickoff view."""

from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse

from devopshero_app.models import (
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


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": [
        "http://testserver/integrations/user/google/callback/",
        "https://humanityrules.io/integrations/user/google/callback/",
    ],
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
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.client.force_login(self.user)
        self.repository = Repository.objects.create(
            organization=self.org,
            provider="github",
            name="hermes",
            full_name="org/hermes",
            default_branch="main",
            clone_url="https://github.com/org/hermes.git",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org,
            name="Engineering",
            slug="engineering",
        )
        self.app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            repository=self.repository,
            name="Hermes",
            slug="hermes",
            app_type=App.AppType.WEB,
            build_strategy=App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        ResourceTag.objects.create(
            organization=self.org,
            resource_type=ResourceTag.ResourceType.APP,
            app=self.app,
            key="owner",
            value=self.user.username,
        )

        self.google_config = IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )

    def _params(self, rd: str) -> dict:
        return {"rd": rd, "app_slug": self.app.slug}

    def test_login_required_when_unauthenticated(self) -> None:
        self.client.logout()
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        # The global LOGIN_URL points at WorkOS — we don't care about the destination,
        # just that the decorator kicked in.
        self.assertNotIn("accounts.google.com", response["Location"])

    def test_redirects_to_google_with_expected_params(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/settings/connections"),
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/auth")

        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], ["cid-123.apps.googleusercontent.com"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["redirect_uri"], ["http://testserver/integrations/user/google/callback/"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", params["scope"][0])
        self.assertIn("openid", params["scope"][0])

    def test_stashes_state_and_payload_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        parsed = urlparse(response["Location"])
        state = parse_qs(parsed.query)["state"][0]

        session = self.client.session
        self.assertEqual(session["google_oauth_state"], state)
        payload = session["google_oauth_payload"]
        self.assertEqual(payload["rd"], "https://hermes.dev.example.com/x")
        self.assertEqual(payload["env_id"], str(self.env.id))
        self.assertEqual(payload["app_slug"], "hermes")
        self.assertEqual(payload["owner_username"], "vmendi")

    def test_exact_zone_host_matches(self) -> None:
        # rd host == env zone (no subdomain) should still match.
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_rejects_rd_with_unknown_host(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.other-domain.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("google_oauth_state", self.client.session)

    def test_rejects_rd_with_suffix_lookalike(self) -> None:
        # "evil-dev.example.com" ends with the zone string "dev.example.com"
        # but is not a subdomain — must be rejected.
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://evil-dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_http_scheme(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="javascript:alert(1)"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_missing_rd(self) -> None:
        response = self.client.get(reverse("integrations_user_google_start"))
        self.assertEqual(response.status_code, 400)

    def test_errors_when_google_integration_not_configured(self) -> None:
        self.google_config.delete()
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("setup_google_oauth_client", response.content.decode())

    def test_fails_when_no_redirect_uri_matches_request_host(self) -> None:
        # OAuth client has registered URIs, but none match this request's host.
        self.google_config.config = {
            **self.google_config.config,
            "redirect_uris": ["https://some-other-host.example.com/integrations/user/google/callback/"],
        }
        self.google_config.save()

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("redirect_uri", response.content.decode())

    def test_picks_matching_redirect_uri_by_request_host(self) -> None:
        # Multiple URIs registered; picks the one matching the request host.
        self.google_config.config = {
            **self.google_config.config,
            "redirect_uris": [
                "https://humanityrules.io/integrations/user/google/callback/",
                "http://testserver/integrations/user/google/callback/",
                "https://devopshero.ngrok.io/integrations/user/google/callback/",
            ],
        }
        self.google_config.save()

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        params = parse_qs(parsed.query)
        self.assertEqual(params["redirect_uri"], ["http://testserver/integrations/user/google/callback/"])

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
            reverse("integrations_user_google_start"),
            self._params(rd="https://anything.com/x"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_rd_pointing_at_stranger_org_env(self) -> None:
        # An env exists in another org whose zone the rd would suffix-match.
        # The authenticated user is not a member of that org, so the start view
        # must refuse to bind the OAuth flow to it — otherwise the credential
        # row would be written against a stranger env (orphan, unreachable
        # post-fix, but still pollution + audit confusion).
        other_org = Organization.objects.create(name="OtherOrg", slug="otherorg")
        other_aws = AWSAccount.objects.create(
            organization=other_org, name="OtherProd", aws_account_id="999988887777",
        )
        Environment.objects.create(
            aws_account=other_aws,
            name="OtherStaging",
            slug="other-staging",
            aws_region="us-east-1",
            shared_alb_hosted_zone="stranger.example.com",
        )

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.stranger.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("google_oauth_state", self.client.session)
        self.assertNotIn("google_oauth_payload", self.client.session)
