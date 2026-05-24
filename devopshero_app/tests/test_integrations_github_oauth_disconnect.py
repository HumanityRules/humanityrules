"""Tests for /integrations/github/disconnect/ - removes stored GitHub grant."""

from unittest.mock import patch

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationUserCredential,
    Organization,
    Repository,
    ResourceTag,
    User,
    Workspace,
)


class _DisconnectTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="GitHub DC Org",
            slug="github-dc-org",
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
            provider=IntegrationUserCredential.Provider.GITHUB,
            credentials={"refresh_token": "stored-refresh-token"},
            config={"scope": "repo"},
        )


class TestDisconnectHappyPath(_DisconnectTestBase):

    def test_disconnect_deletes_row_and_redirects(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.github_oauth._revoke_github_grant"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/github/disconnect/?rd={rd}&app_slug=hermes")

        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=github", response["Location"])
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertFalse(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_called_once_with(refresh_token="stored-refresh-token")


class TestDisconnectIdempotent(_DisconnectTestBase):

    def test_disconnect_without_existing_row_still_redirects(self) -> None:
        self.integration.delete()
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.github_oauth._revoke_github_grant"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/github/disconnect/?rd={rd}&app_slug=hermes")

        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=github", response["Location"])
        revoke_mock.assert_not_called()


class TestDisconnectRdValidation(_DisconnectTestBase):

    def test_bad_rd_host_returns_400(self) -> None:
        response = self.client.get(
            "/integrations/github/disconnect/?rd=https://attacker.example.net/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_missing_rd_returns_400(self) -> None:
        response = self.client.get("/integrations/github/disconnect/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )

    def test_missing_app_slug_returns_400(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.github_oauth._revoke_github_grant"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/github/disconnect/?rd={rd}")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_not_called()

    def test_unauthorized_app_slug_returns_400(self) -> None:
        rd = "https://hermes.dev.example.com/settings"
        with patch(
            "devopshero_app.views.integrations.github_oauth._revoke_github_grant"
        ) as revoke_mock:
            response = self.client.get(f"/integrations/github/disconnect/?rd={rd}&app_slug=other")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_not_called()


class TestDisconnectAuth(_DisconnectTestBase):

    def test_logged_out_redirects_to_login(self) -> None:
        self.client.logout()
        response = self.client.get(
            "/integrations/github/disconnect/?rd=https://hermes.dev.example.com/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
