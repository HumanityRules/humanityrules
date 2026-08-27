"""Tests for POST /api/integrations/credentials/disconnect — broker OAuth disconnect (app + owner from the bearer)."""

import json
from unittest.mock import patch

from django.test import Client, TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


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
        self.user = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.workspace = Workspace.objects.create(
            organization=self.org,
            name="Engineering",
            slug="engineering",
        )
        self.app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            source_template=make_source_template(),
            name="Hermes",
            slug="hermes",
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
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="test-bearer")
        self.integration = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug=self.app.slug,
            provider=IntegrationUserCredential.Provider.GITHUB,
            credentials={"refresh_token": "stored-refresh-token"},
            config={"scope": "repo"},
        )

    def _post(self, payload: dict, bearer: str | None):
        headers = bearer_test_helpers.auth_header(raw=bearer) if bearer is not None else {}
        return self.client.post(
            "/api/integrations/credentials/disconnect",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def _make_app(self, slug: str, owner_username: str | None) -> App:
        app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            name=slug, slug=slug, environment=self.env,
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )
        if owner_username is not None:
            ResourceTag.objects.create(
                organization=self.org, resource_type=ResourceTag.ResourceType.APP,
                app=app, key="owner", value=owner_username,
            )
        return app

    def test_disconnect_deletes_row(self) -> None:
        with patch(
            "humanityrules_app.views.integrations.provider_github.revoke"
        ) as revoke_mock:
            response = self._post({"provider": "github"}, bearer=self.raw_token)

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
            response = self._post({"provider": "google"}, bearer=self.raw_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "status": "not_connected"})
        row = IntegrationUserCredential.objects.get(id=self.integration.id)
        self.assertEqual(row.credentials, {})
        self.assertEqual(row.config, {"scope": ""})
        self.assertIn("revoked_at_epoch", row.metadata)
        revoke_mock.assert_called_once_with(refresh_token="stored-refresh-token")

    def test_missing_bearer_returns_401(self) -> None:
        response = self._post({"provider": "github"}, bearer=None)
        self.assertEqual(response.status_code, 401)

    def test_identity_in_body_is_ignored_in_favour_of_the_bearers_app(self) -> None:
        other = self._make_app(slug="theirs", owner_username="someone-else")
        with patch("humanityrules_app.views.integrations.provider_github.revoke"):
            response = self._post(
                {"owner_username": "someone-else", "app_slug": other.slug, "provider": "github"},
                bearer=self.raw_token,
            )
        self.assertEqual(response.status_code, 200)
        # The bearer's own credential row is what was disconnected.
        self.assertFalse(IntegrationUserCredential.objects.filter(id=self.integration.id).exists())

    def test_app_without_owner_returns_404(self) -> None:
        lonely = self._make_app(slug="lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="lonely-bearer")
        response = self._post({"provider": "github"}, bearer=lonely_token)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(IntegrationUserCredential.objects.filter(id=self.integration.id).exists())

    def test_unsupported_provider_returns_400(self) -> None:
        # An unregistered slug is rejected. Registered providers of any kind
        # (OAuth or vault) are valid through this unified endpoint.
        response = self._post({"provider": "bogus-provider"}, bearer=self.raw_token)
        self.assertEqual(response.status_code, 400)

