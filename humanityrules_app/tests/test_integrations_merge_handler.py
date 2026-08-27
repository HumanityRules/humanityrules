"""Tests for HUMR-side Merge Agent Handler identity scoping.

The Merge endpoints derive the (App, owner) pair from the per-app bearer and
build `origin_user_id` from it. Nothing a caller sends — `X-Humr-*` headers,
query string, or body — can redirect the call at another app or owner.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


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
        self.app = self._make_app(slug="hermes", owner_username=self.owner.username)
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="m" * 64)
        self.client = Client()

    def _make_app(self, slug: str, owner_username: str | None) -> App:
        app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            source_template=make_source_template(),
            name=slug,
            slug=slug,
            environment=self.env,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        if owner_username is not None:
            ResourceTag.objects.create(
                organization=self.org,
                resource_type=ResourceTag.ResourceType.APP,
                app=app,
                key="owner",
                value=owner_username,
            )
        return app

    def _post_ensure(self, body: dict, token: str | None, extra_headers: dict) -> tuple[int, dict]:
        headers = bearer_test_helpers.auth_header(raw=token) if token is not None else {}
        response = self.client.post(
            "/api/integrations/merge/ensure-registered-user",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
            **extra_headers,
        )
        return response.status_code, response.json()

    def _patched_merge_create(self) -> object:
        response = MagicMock()
        response.status_code = 201
        response.json.return_value = {"id": "11111111-1111-1111-1111-111111111111"}
        return patch(
            "humanityrules_app.views.integrations.merge_handler.httpx.post",
            return_value=response,
        )

    def test_missing_bearer_returns_401(self) -> None:
        status, _body = self._post_ensure(body={}, token=None, extra_headers={})
        self.assertEqual(status, 401)

    def test_bearer_resolves_the_apps_owner(self) -> None:
        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(body={}, token=self.raw_token, extra_headers={})

        self.assertEqual(status, 200)
        self.assertEqual(body["registered_user_id"], "11111111-1111-1111-1111-111111111111")
        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.kwargs["json"]["origin_user_id"], f"humr_{self.org.pk}_{self.owner.pk}_hermes")

    def test_headers_and_body_naming_another_app_and_owner_are_ignored(self) -> None:
        alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(user=alice, organization=self.org, role=OrganizationMembership.Role.MEMBER)
        self._make_app(slug="theirs", owner_username="alice")

        with self._patched_merge_create() as post_mock:
            status, _body = self._post_ensure(
                body={"owner_username": "alice", "app_slug": "theirs"},
                token=self.raw_token,
                extra_headers={"HTTP_X_HUMR_APP_SLUG": "theirs", "HTTP_X_HUMR_OWNER_USERNAME": "alice"},
            )

        # The bearer's own (vmendi, hermes) identity is what reaches Merge.
        self.assertEqual(status, 200)
        self.assertEqual(post_mock.call_args.kwargs["json"]["origin_user_id"], f"humr_{self.org.pk}_{self.owner.pk}_hermes")

    def test_app_without_owner_returns_404(self) -> None:
        lonely = self._make_app(slug="lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="l" * 64)

        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(body={}, token=lonely_token, extra_headers={})

        self.assertEqual(status, 404)
        self.assertIn("error", body)
        post_mock.assert_not_called()

    def test_owner_from_another_org_does_not_satisfy_owner_identity(self) -> None:
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
        ResourceTag.objects.filter(app=self.app, key="owner").update(value="outsider")

        with self._patched_merge_create() as post_mock:
            status, body = self._post_ensure(body={}, token=self.raw_token, extra_headers={})

        self.assertEqual(status, 404)
        self.assertIn("error", body)
        post_mock.assert_not_called()

    def test_same_user_same_slug_in_another_org_gets_a_distinct_merge_identity(self) -> None:
        """`user.pk` is global and slugs repeat across orgs; only the org id keeps the vaults apart."""
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        other_aws = AWSAccount.objects.create(organization=other_org, name="Other AWS")
        other_env = Environment.objects.create(
            aws_account=other_aws, name="Staging", slug="staging", aws_region="us-east-1",
        )
        other_workspace = Workspace.objects.create(organization=other_org, name="Assistants", slug="assistants")
        OrganizationMembership.objects.create(
            user=self.owner, organization=other_org, role=OrganizationMembership.Role.MEMBER,
        )
        other_app = App.objects.create(
            organization=other_org,
            workspace=other_workspace,
            source_template=make_source_template(),
            name="hermes",
            slug="hermes",
            environment=other_env,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        ResourceTag.objects.create(
            organization=other_org,
            resource_type=ResourceTag.ResourceType.APP,
            app=other_app,
            key="owner",
            value=self.owner.username,
        )
        other_token = bearer_test_helpers.make_app_bearer(app=other_app, raw="o" * 64)

        with self._patched_merge_create() as post_mock:
            status, _body = self._post_ensure(body={}, token=other_token, extra_headers={})

        self.assertEqual(status, 200)
        sent = post_mock.call_args.kwargs["json"]["origin_user_id"]
        self.assertEqual(sent, f"humr_{other_org.pk}_{self.owner.pk}_hermes")
        self.assertNotEqual(sent, f"humr_{self.org.pk}_{self.owner.pk}_hermes")
