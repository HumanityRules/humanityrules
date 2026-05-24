"""Tests for DOH-side Merge Agent Handler identity scoping."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from devopshero_app.models import (
    AWSAccount,
    App,
    Environment,
    EnvironmentBearerToken,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@override_settings(MERGE_AGENT_HANDLER_API_KEY="mah_test_key")
class TestMergeCallerResolution(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Merge Org", slug="merge-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Merge AWS")
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org,
            name="Assistants",
            slug="assistants",
        )
        self.repository = Repository.objects.create(
            organization=self.org,
            provider="github",
            name="hermes",
            full_name="org/hermes",
            default_branch="main",
            clone_url="https://github.com/org/hermes.git",
        )
        self.owner = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.org,
            role=OrganizationMembership.Role.MEMBER,
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
            value=self.owner.username,
        )
        self.raw_token = "m" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.env,
            token_hash=_hash(self.raw_token),
        )
        self.client = Client()

    def _post_ensure(self, owner_username: str) -> tuple[int, dict]:
        response = self.client.post(
            "/api/integrations/merge/ensure-registered-user",
            data=json.dumps({
                "owner_username": owner_username,
                "app_slug": self.app.slug,
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        return response.status_code, response.json()

    def _patched_merge_create(self) -> object:
        response = MagicMock()
        response.status_code = 201
        response.json.return_value = {"id": "11111111-1111-1111-1111-111111111111"}
        return patch(
            "devopshero_app.views.integrations.merge_handler.httpx.post",
            return_value=response,
        )

    def test_owner_member_can_resolve_merge_registered_user(self) -> None:
        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(owner_username="vmendi")

        self.assertEqual(status, 200)
        self.assertEqual(body["registered_user_id"], "11111111-1111-1111-1111-111111111111")
        post_mock.assert_called_once()

    def test_user_from_another_org_does_not_satisfy_owner_identity(self) -> None:
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        outsider = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="pw",
            current_organization=other_org,
        )
        OrganizationMembership.objects.create(
            user=outsider,
            organization=other_org,
            role=OrganizationMembership.Role.MEMBER,
        )
        ResourceTag.objects.filter(
            organization=self.org,
            resource_type=ResourceTag.ResourceType.APP,
            app=self.app,
            key="owner",
        ).update(value="outsider")

        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(owner_username="outsider")

        self.assertEqual(status, 404)
        self.assertIn("error", body)
        post_mock.assert_not_called()

    def test_non_owner_member_cannot_resolve_app_merge_identity(self) -> None:
        alice = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=alice,
            organization=self.org,
            role=OrganizationMembership.Role.MEMBER,
        )

        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(owner_username="alice")

        self.assertEqual(status, 403)
        self.assertIn("error", body)
        post_mock.assert_not_called()
