"""Tests for POST /api/integrations/credentials/disconnect — broker OAuth disconnect."""

import hashlib
import json
from unittest.mock import patch

from django.test import Client, TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    EnvironmentBearerToken,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TestUserOAuthDisconnectApi(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="OAuth DC Org",
            slug="oauth-dc-org",
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
        self.raw_token = "test-bearer"
        EnvironmentBearerToken.objects.create(
            environment=self.env,
            token_hash=_hash(self.raw_token),
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
            environment=self.env,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
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

    def _post(self, payload: dict, bearer: str | None = None):
        headers = {}
        if bearer is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {bearer}"
        return self.client.post(
            "/api/integrations/credentials/disconnect",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def test_disconnect_deletes_row(self) -> None:
        with patch(
            "humanityrules_app.views.integrations.provider_github.revoke"
        ) as revoke_mock:
            response = self._post({
                "owner_username": "vmendi",
                "app_slug": "hermes",
                "provider": "github",
            }, bearer=self.raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "status": "not_connected"})
        self.assertFalse(
            IntegrationUserCredential.objects.filter(id=self.integration.id).exists()
        )
        revoke_mock.assert_called_once_with(refresh_token="stored-refresh-token")

    def test_google_disconnect_tombstones_instead_of_deleting(self) -> None:
        """Google keeps a revocation tombstone so pending OAuth callbacks can't resurrect the grant."""
        self.integration.provider = IntegrationUserCredential.Provider.GOOGLE
        self.integration.save(update_fields=["provider", "updated_at"])

        with patch(
            "humanityrules_app.views.integrations.provider_google.revoke"
        ) as revoke_mock:
            response = self._post({
                "owner_username": "vmendi",
                "app_slug": "hermes",
                "provider": "google",
            }, bearer=self.raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "status": "not_connected"})
        row = IntegrationUserCredential.objects.get(id=self.integration.id)
        self.assertEqual(row.credentials, {})
        self.assertEqual(row.config, {"scope": ""})
        self.assertIn("revoked_at_epoch", row.metadata)
        revoke_mock.assert_called_once_with(refresh_token="stored-refresh-token")

    def test_missing_bearer_returns_401(self) -> None:
        response = self.client.post(
            "/api/integrations/credentials/disconnect",
            data={"owner_username": "vmendi", "app_slug": "hermes", "provider": "github"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_unsupported_provider_returns_400(self) -> None:
        # An unregistered slug is rejected. Registered providers of any kind
        # (OAuth or vault) are valid through this unified endpoint.
        response = self._post({
            "owner_username": "vmendi",
            "app_slug": "hermes",
            "provider": "bogus-provider",
        }, bearer=self.raw_token)
        self.assertEqual(response.status_code, 400)

