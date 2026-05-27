"""Tests for /integrations/user/google/disconnect/ — removes stored Google grant."""

from unittest.mock import patch

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
)


class _DisconnectTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="DCOrg",
            slug="dcorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Prod", aws_account_id="111122223333",
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
        self.integration = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug=self.app.slug,
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "stored-refresh-token"},
            config={"scope": "openid email"},
        )


class TestDisconnectHappyPath(_DisconnectTestBase):

    def test_disconnect_deletes_row_and_redirects(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/user/google/disconnect/?rd={rd}&app_slug=hermes")

        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertFalse(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        # Best-effort revoke invoked with the stored token.
        revoke_mock.assert_called_once()
        args, kwargs = revoke_mock.call_args
        self.assertEqual(args[0], "https://oauth2.googleapis.com/revoke")
        self.assertEqual(kwargs["data"]["token"], "stored-refresh-token")


class TestDisconnectIdempotent(_DisconnectTestBase):

    def test_disconnect_without_existing_row_still_redirects(self) -> None:
        self.integration.delete()
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/user/google/disconnect/?rd={rd}&app_slug=hermes")

        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        # No revoke attempt when there was nothing to revoke.
        revoke_mock.assert_not_called()


class TestDisconnectRdValidation(_DisconnectTestBase):

    def test_bad_rd_host_returns_400(self) -> None:
        response = self.client.get(
            "/integrations/user/google/disconnect/?rd=https://attacker.example.net/"
        )
        self.assertEqual(response.status_code, 400)
        # Row preserved — we refused the request before touching state.
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_rd_pointing_at_stranger_org_env_returns_400(self) -> None:
        # An env in a foreign org happens to use a hosted zone the rd suffix-matches.
        # The auth'd user is not a member of that org — disconnect must refuse.
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
            "/integrations/user/google/disconnect/?rd=https://hermes.stranger.example.com/&app_slug=hermes"
        )
        self.assertEqual(response.status_code, 400)
        # Our own row is untouched.
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_missing_app_slug_returns_400(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/user/google/disconnect/?rd={rd}")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_not_called()

    def test_unauthorized_app_slug_returns_400(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/user/google/disconnect/?rd={rd}&app_slug=other")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_not_called()

    def test_missing_rd_returns_400(self) -> None:
        response = self.client.get("/integrations/user/google/disconnect/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )


class TestDisconnectRevokeFailureNonFatal(_DisconnectTestBase):

    def test_revoke_network_error_still_deletes_and_redirects(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.google_oauth.httpx.post",
            side_effect=Exception("network down"),
        ):
            response = self.client.get(f"/integrations/user/google/disconnect/?rd={rd}&app_slug=hermes")

        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        # Row still gone — the row deletion is load-bearing, the revoke is best-effort.
        self.assertFalse(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )


class TestDisconnectAuth(_DisconnectTestBase):

    def test_logged_out_redirects_to_login(self) -> None:
        self.client.logout()
        response = self.client.get(
            "/integrations/user/google/disconnect/?rd=https://hermes.dev.example.com/"
        )
        self.assertEqual(response.status_code, 302)
        # login_required redirects to settings.LOGIN_URL. The row must survive.
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
