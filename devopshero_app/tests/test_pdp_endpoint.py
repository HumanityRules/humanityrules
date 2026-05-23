"""Tests for the PDP HTTP endpoint (devopshero_app/views/pdp.py)."""

import hashlib
import json

from django.test import Client, TestCase

from devopshero_app.models import (
    App,
    AWSAccount,
    DeploymentBlueprint,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    Repository,
    ResourceTag,
    EnvironmentBearerToken,
    User,
    Workspace,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="hermes",
            full_name="org/hermes", clone_url="https://github.com/org/hermes.git",
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
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name="VmendiPA", slug="vmendi-hermes", app_type="web",
            build_strategy="dockerfile", branch="main", container_port=8000,
            health_check_path="/health",
        )
        # Drop the auto-created open-access policy; the self-ref policy is what we test.
        Policy.objects.filter(
            organization=self.org, name=f"Default: {self.app.name} open access",
        ).delete()
        DeploymentBlueprint.objects.create(
            app=self.app, environment=self.environment,
            status=DeploymentBlueprint.Status.ACTIVE,
            cpu=256, memory=512,
        )
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
        self.raw_token = "t" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.environment, token_hash=_hash(self.raw_token),
        )
        self.client = Client()

    def _post(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
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
                "app_id": "vmendi-hermes",
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
                "app_id": "vmendi-hermes",
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

    def test_unknown_app_returns_deny_app_not_in_org(self) -> None:
        status, body = self._post(
            body={
                "app_id": "does-not-exist",
                "provider": "oidc",
                "sub": "okta|vmendi",
                "username": "vmendi",
                "path": "/",
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "app-not-in-org")

    def test_app_in_different_env_returns_deny_app_not_in_env(self) -> None:
        other_env = Environment.objects.create(
            aws_account=self.aws_account, name="prod", slug="prod",
            aws_region="us-east-1",
        )
        other_token_raw = "u" * 64
        EnvironmentBearerToken.objects.create(
            environment=other_env, token_hash=_hash(other_token_raw),
        )
        # The app exists in the org but has no blueprint for other_env.
        status, body = self._post(
            body={
                "app_id": "vmendi-hermes",
                "provider": "oidc",
                "sub": "okta|vmendi",
                "username": "vmendi",
                "path": "/",
            },
            token=other_token_raw,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "app-not-in-env")

    def test_unknown_oidc_sub_returns_deny(self) -> None:
        status, body = self._post(
            body={
                "app_id": "vmendi-hermes",
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
                "app_id": "vmendi-hermes",
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
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name="Open App", slug="open-app", app_type="web",
            build_strategy="dockerfile", branch="main", container_port=8000,
            health_check_path="/health",
        )
        DeploymentBlueprint.objects.create(
            app=open_app, environment=self.environment,
            status=DeploymentBlueprint.Status.ACTIVE,
            cpu=256, memory=512,
        )

        status, body = self._post(
            body={
                "app_id": "open-app",
                "provider": "oidc",
                "sub": "okta|outsider",
                "username": "outsider",
                "path": "/",
            },
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "deny")
        self.assertEqual(body["reason"], "user-not-found")

    def test_workos_owner_receives_allow(self) -> None:
        """A user looked up by workos_user_id is authorized identically to the OIDC path."""
        self.owner.workos_user_id = "user_01H_vmendi"
        self.owner.save()
        status, body = self._post(
            body={
                "app_id": "vmendi-hermes",
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
                "app_id": "vmendi-hermes",
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
            body={"app_id": "vmendi-hermes"},
            token=self.raw_token,
        )
        self.assertEqual(status, 400)
