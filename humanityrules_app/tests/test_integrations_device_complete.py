"""Tests for broker-facing OAuth device-flow completion."""

import hashlib
import json

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
        self.repository = Repository.objects.create(
            organization=self.org,
            provider=Repository.Provider.GITHUB,
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
        self.raw_token = "d" * 64
        EnvironmentBearerToken.objects.create(environment=self.env, token_hash=_hash(raw=self.raw_token))
        self.client = Client()

    def _post(self, provider: str, body: dict) -> tuple[int, dict]:
        response = self.client.post(
            f"/api/integrations/credentials/{provider}/device-complete",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        return response.status_code, response.json()

    def test_nous_device_complete_stores_refresh_token(self) -> None:
        status, body = self._post(
            provider="nous",
            body={
                "owner_username": self.user.username,
                "app_slug": self.app.slug,
                "refresh_token": "nous-refresh",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["provider"], "nous")
        credential = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.NOUS)
        self.assertEqual(credential.credentials, {"refresh_token": "nous-refresh"})
        self.assertEqual(set(credential.metadata), {"connected_at"})

    def test_unknown_device_provider_returns_404(self) -> None:
        status, body = self._post(
            provider="openrouter",
            body={
                "owner_username": self.user.username,
                "app_slug": self.app.slug,
                "refresh_token": "not-used",
            },
        )

        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "unknown device-flow provider")
