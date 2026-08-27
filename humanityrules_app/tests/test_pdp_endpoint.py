"""Tests for the PDP HTTP endpoint (humanityrules_app/views/pdp.py).

The app whose policies are evaluated comes from the caller's per-app bearer
token, so the body carries only the end user's claims. The old app-not-in-org /
app-not-in-env denials are gone with the caller-supplied app_id; what replaces
them is the pair of tests proving an app_id in the body cannot redirect the
decision to another app.
"""

import json

from django.test import Client, TestCase

from humanityrules_app.models import (
    App,
    AWSAccount,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


class PDPTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="PDP Org", slug="pdp-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="PDP Account")
        self.environment = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org, name="PAs", slug="pas",
        )
        self.owner = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
            oidc_sub="okta|vmendi",
        )
        self.stranger = User.objects.create_user(
            username="alice", password="pw", current_organization=self.org,
            oidc_sub="okta|alice",
        )
        OrganizationMembership.objects.create(
            user=self.owner, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        OrganizationMembership.objects.create(
            user=self.stranger, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.environment, name="VmendiPA", slug="vmendihermes",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        # Drop the auto-created open-access policy; the self-ref policy is what we test.
        Policy.objects.filter(
            organization=self.org, name=f"Default: {self.app.name} open access",
        ).delete()
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.app,
            key="app-type", value="personal-assistant",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.app,
            key="owner", value="vmendi",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.owner, key="username", value="vmendi",
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.stranger, key="username", value="alice",
        )
        Policy.objects.create(
            organization=self.org, name="PA owner access",
            resource_type="app",
            identity_conditions=[{"key": "username", "value": "$resource.owner"}],
            resource_conditions=[{"key": "app-type", "value": "personal-assistant"}],
            actions=["app:use"],
        )
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="t" * 64)
        self.client = Client()

    def _post(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = bearer_test_helpers.auth_header(raw=token) if token is not None else {}
        response = self.client.post(
            "/api/pdp/evaluate",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()


class TestPDPAuthentication(PDPTestBase):

    def test_missing_bearer_token_returns_401(self) -> None:
        status, body = self._post(body={}, token=None)
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_token_returns_401(self) -> None:
        status, body = self._post(body={}, token="nope")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_app_whose_org_differs_from_environment_org_returns_401(self) -> None:
        """A valid bearer fails closed when the App's org drifts from its Environment's org."""
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        App.objects.filter(pk=self.app.pk).update(organization=other_org)
        status, body = self._post(body={}, token=self.raw_token)
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_invalid_json_returns_400(self) -> None:
        response = self.client.post(
            "/api/pdp/evaluate",
            data="not-json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        self.assertEqual(response.status_code, 400)


class TestPDPEvaluation(PDPTestBase):

    def test_owner_receives_allow(self) -> None:
        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|vmendi",
                "username": "vmendi",
                "path": "/chat/new",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "allow")

    def test_non_owner_receives_deny(self) -> None:
        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|alice",
                "username": "alice",
                "path": "/chat/new",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "no-matching-policy")

    def test_body_app_id_cannot_redirect_the_decision(self) -> None:
        # A second app in the same org whose owner tag names alice. Naming it in
        # the body must not get alice an allow — the bearer's app is what the
        # policies are evaluated against, and vmendi owns that one.
        other = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.environment, name="AlicePA", slug="alicehermes",
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other,
            key="app-type", value="personal-assistant",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other, key="owner", value="alice",
        )
        Policy.objects.filter(organization=self.org, name=f"Default: {other.name} open access").delete()

        status, body = self._post(
            body={
                "app_id": other.slug,
                "provider": "oidc",
                "sub": "okta|alice",
                "username": "alice",
                "path": "/",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "no-matching-policy")

    def test_the_other_apps_bearer_evaluates_that_apps_policies(self) -> None:
        # Same pair as above, this time presenting the other app's own token —
        # alice owns it, so she is allowed. Isolation cuts both ways.
        other = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.environment, name="AlicePA", slug="alicehermes",
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other,
            key="app-type", value="personal-assistant",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=other, key="owner", value="alice",
        )
        Policy.objects.filter(organization=self.org, name=f"Default: {other.name} open access").delete()
        other_token = bearer_test_helpers.make_app_bearer(app=other, raw="u" * 64)

        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|alice",
                "username": "alice",
                "path": "/",
            },
            token=other_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "allow")

    def test_unknown_oidc_sub_returns_deny(self) -> None:
        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|ghost",
                "username": "ghost",
                "path": "/",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "user-not-found")

    def test_unknown_workos_sub_returns_deny(self) -> None:
        """A WorkOS sub that doesn't match any User.workos_user_id is user-not-found."""
        status, body = self._post(
            body={
                "provider": "workos",
                "sub": "user_01H_unknown",
                "username": "ghost@example.com",
                "path": "/",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "user-not-found")

    def test_user_from_another_org_does_not_satisfy_open_app_policy(self) -> None:
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        outsider = User.objects.create_user(
            username="outsider",
            password="pw",
            current_organization=other_org,
            oidc_sub="okta|outsider",
        )
        OrganizationMembership.objects.create(
            user=outsider, organization=other_org, role=OrganizationMembership.Role.MEMBER,
        )
        open_app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.environment, name="Open App", slug="open-app",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        open_app_token = bearer_test_helpers.make_app_bearer(app=open_app, raw="o" * 64)

        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|outsider",
                "username": "outsider",
                "path": "/",
            },
            token=open_app_token,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "user-not-found")

    def test_superuser_from_another_org_receives_platform_admin_allow(self) -> None:
        other_org = Organization.objects.create(name="Admin Org", slug="admin-org")
        superuser = User.objects.create_user(
            username="platform-admin",
            password="pw",
            current_organization=other_org,
            oidc_sub="okta|platform-admin",
            is_superuser=True,
        )
        OrganizationMembership.objects.create(
            user=superuser, organization=other_org, role=OrganizationMembership.Role.MEMBER,
        )

        status, body = self._post(
            body={
                "provider": "oidc",
                "sub": "okta|platform-admin",
                "username": "platform-admin",
                "path": "/chat/new",
            },
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "allow")
        self.assertEqual(body["reason"], "platform-admin")

    def test_workos_owner_receives_allow(self) -> None:
        """A user looked up by workos_user_id is authorized identically to the OIDC path."""
        self.owner.workos_user_id = "user_01H_vmendi"
        self.owner.save()
        status, body = self._post(
            body={
                "provider": "workos",
                "sub": "user_01H_vmendi",
                "username": "vmendi",
                "path": "/chat/new",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "allow")

    def test_unknown_provider_returns_400(self) -> None:
        status, body = self._post(
            body={
                "provider": "facebook",
                "sub": "x",
                "username": "x",
                "path": "/",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 400)

    def test_missing_required_fields_returns_400(self) -> None:
        status, body = self._post(
            body={},
            token=self.raw_token,
        )
        self.assertEqual(status, 400)
