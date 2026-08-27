"""Tests for broker-facing OAuth device-flow completion (app + owner from the per-app bearer)."""

import json

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


class TestDeviceComplete(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Device Org", slug="device-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Device Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="default",
            slug="default",
            aws_region="us-east-1",
        )
        self.user = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.org,
            role=OrganizationMembership.Role.MEMBER,
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
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="d" * 64)
        self.client = Client()

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

    def _post(self, provider: str, body: dict, token: str) -> tuple[int, dict]:
        response = self.client.post(
            f"/api/integrations/credentials/{provider}/device-complete",
            data=json.dumps(body),
            content_type="application/json",
            **bearer_test_helpers.auth_header(raw=token),
        )
        return response.status_code, response.json()

    def test_nous_device_complete_stores_refresh_token(self) -> None:
        status, body = self._post(provider="nous", body={"refresh_token": "nous-refresh"}, token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["provider"], "nous")
        credential = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.NOUS)
        self.assertEqual(credential.owner_user, self.user)
        self.assertEqual(credential.app_slug, self.app.slug)
        self.assertEqual(credential.credentials, {"refresh_token": "nous-refresh"})
        self.assertEqual(set(credential.metadata), {"connected_at"})

    def test_identity_in_body_is_ignored_in_favour_of_the_bearers_app(self) -> None:
        other = self._make_app(slug="theirs", owner_username="someone-else")

        status, _body = self._post(
            provider="nous",
            body={"owner_username": "someone-else", "app_slug": other.slug, "refresh_token": "nous-refresh"},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        credential = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.NOUS)
        self.assertEqual(credential.owner_user, self.user)
        self.assertEqual(credential.app_slug, self.app.slug)

    def test_app_without_owner_returns_404(self) -> None:
        lonely = self._make_app(slug="lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="l" * 64)

        status, _body = self._post(provider="nous", body={"refresh_token": "nous-refresh"}, token=lonely_token)

        self.assertEqual(status, 404)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_unknown_device_provider_returns_404(self) -> None:
        status, body = self._post(provider="openrouter", body={"refresh_token": "not-used"}, token=self.raw_token)

        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown device-flow provider")
