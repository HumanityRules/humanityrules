"""Tests for the Owner field on the template deploy form (PA templates)."""

from unittest.mock import patch

from django.test import Client, TestCase

from devopshero_app.models import (
    AppTemplate,
    AWSAccount,
    Environment,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from devopshero_app.services import abac


def _make_pa_template() -> AppTemplate:
    return AppTemplate.objects.create(
        name="AI Assistant — Hermes Docker (Personal)",
        slug="hermes-docker-personal",
        description="Personal AI assistant.",
        icon="⚡",
        category="ai-assistant",
        cpu=1024,
        memory=2048,
        default_compute_mode="ec2",
        alb_target_container="policy-proxy",
        containers=[
            {
                "name": "docker-dind",
                "image_source": "registry",
                "registry_image": "docker:26.1.0-dind",
                "container_port": 0,
                "health_check_path": None,
                "health_check_command": "docker info >/dev/null 2>&1",
                "health_check_grace_period": 120,
                "efs_mounts": ["workspace"],
                "configurable_variables": [],
            },
            {
                "name": "hermes",
                "image_source": "dockerfile",
                "source_repo_path": "hermes_docker_agent",
                "dockerfile_path": "Dockerfile",
                "container_port": 8787,
                "health_check_path": "/health",
                "health_check_command": "",
                "health_check_grace_period": 60,
                "efs_mounts": ["home", "workspace"],
                "configurable_variables": [],
            },
            {
                "name": "policy-proxy",
                "image_source": "policy_proxy",
                "upstream_container": "hermes",
                "container_port": 8788,
                "health_check_path": "/__policy_proxy/healthz",
                "health_check_command": "",
                "health_check_grace_period": 0,
                "configurable_variables": [],
            },
        ],
        datastore_config=None,
        efs_config={
            "mounts": [
                {"name": "home", "subpath": "hermes", "container_path": "/home/hermeswebui/.hermes",
                 "posix_uid": 1024, "posix_gid": 1024},
                {"name": "workspace", "subpath": "workspace", "container_path": "/workspace",
                 "posix_uid": 1024, "posix_gid": 1024},
            ],
        },
        default_tags=[{"key": "app-type", "value": "personal-assistant"}],
        is_active=True,
    )


class DeployFormOwnerBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="PA Org", slug="pa-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Account")
        self.environment = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1", status=Environment.Status.READY,
        )
        self.workspace = Workspace.objects.get(organization=self.org, slug="default")
        self.admin = User.objects.create_user(
            username="admin-user", password="pw", email="admin@example.com",
            current_organization=self.org,
        )
        self.alice = User.objects.create_user(
            username="alice", password="pw", email="alice@example.com",
            current_organization=self.org,
        )
        self.bob = User.objects.create_user(
            username="bob", password="pw", email="bob@example.com",
            current_organization=self.org,
        )
        for u in (self.admin, self.alice, self.bob):
            OrganizationMembership.objects.create(
                user=u, organization=self.org,
                role=OrganizationMembership.Role.MEMBER,
            )
        # Bootstrap ABAC so workspace/env policies exist for everyone.
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin)
        # Grant Alice and Bob member-role so they can see workspace/env.
        abac.assign_default_org_role(organization=self.org, user=self.alice)
        abac.assign_default_org_role(organization=self.org, user=self.bob)
        self.template = _make_pa_template()
        self.client = Client()

    def _login(self, user: User) -> None:
        self.client.force_login(user)


class TestOwnerFieldPresence(DeployFormOwnerBase):

    def test_non_admin_sees_owner_locked_to_self(self) -> None:
        self._login(self.alice)
        response = self.client.get(
            f"/deploy/from-template/{self.template.slug}/",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Owner", response.content)
        self.assertIn(b"Compute", response.content)
        self.assertIn(b"EC2 Capacity", response.content)
        self.assertIn(b"registry", response.content)
        self.assertIn(b"docker:26.1.0-dind", response.content)
        self.assertNotIn(b"doh/{env}/:", response.content)
        # Hidden input carries alice's username.
        self.assertIn(b'name="owner_id" value="alice"', response.content)
        # No dropdown option list that includes Bob.
        self.assertNotIn(b">bob<", response.content)

    def test_admin_sees_full_dropdown(self) -> None:
        self._login(self.admin)
        response = self.client.get(
            f"/deploy/from-template/{self.template.slug}/",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Owner", response.content)
        # Dropdown visible (no hidden input).
        self.assertNotIn(b'type="hidden"\n                    name="owner_id"', response.content)
        self.assertIn(b"alice", response.content)
        self.assertIn(b"bob", response.content)


class TestOwnerFieldSubmission(DeployFormOwnerBase):

    @patch("devopshero_app.views.template_deploy.async_to_sync")
    def test_non_admin_submission_forces_self_as_owner(self, mock_async_to_sync) -> None:
        # Capture the call args; short-circuit the deploy so we don't actually run it.
        captured: dict[str, object] = {}

        def capture(fn):
            def runner(**kwargs):
                captured.update(kwargs)
                # Return an object with .app.slug so the view's redirect works.
                class _A: pass
                a = _A(); a.app = _A(); a.app.slug = kwargs["app_slug"]
                return a
            return runner

        mock_async_to_sync.side_effect = capture

        self._login(self.alice)
        response = self.client.post(
            f"/deploy/from-template/{self.template.slug}/",
            data={
                "app_name": "Alice PA",
                "workspace_id": str(self.workspace.id),
                "environment_id": str(self.environment.id),
                # Alice is a non-admin; whatever she submits must be ignored.
                "owner_id": "bob",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(captured["owner_username"], "alice")
        self.assertEqual(captured["compute_mode"], "ec2")

    @patch("devopshero_app.views.template_deploy.async_to_sync")
    def test_admin_can_pick_another_owner(self, mock_async_to_sync) -> None:
        captured: dict[str, object] = {}

        def capture(fn):
            def runner(**kwargs):
                captured.update(kwargs)
                class _A: pass
                a = _A(); a.app = _A(); a.app.slug = kwargs["app_slug"]
                return a
            return runner

        mock_async_to_sync.side_effect = capture

        self._login(self.admin)
        response = self.client.post(
            f"/deploy/from-template/{self.template.slug}/",
            data={
                "app_name": "Bob PA",
                "workspace_id": str(self.workspace.id),
                "environment_id": str(self.environment.id),
                "owner_id": "bob",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(captured["owner_username"], "bob")
        self.assertEqual(captured["compute_mode"], "ec2")

    def test_admin_rejecting_unknown_owner(self) -> None:
        self._login(self.admin)
        response = self.client.post(
            f"/deploy/from-template/{self.template.slug}/",
            data={
                "app_name": "Ghost PA",
                "workspace_id": str(self.workspace.id),
                "environment_id": str(self.environment.id),
                "owner_id": "not-a-user",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Selected owner is not a member", response.content)


class TestOwnerTagStampedOnDeploy(DeployFormOwnerBase):

    def test_owner_tag_written_when_owner_submitted(self) -> None:
        # Integration-style: run the real deploy_from_template path and check
        # the ResourceTag row was created.
        self._login(self.alice)
        response = self.client.post(
            f"/deploy/from-template/{self.template.slug}/",
            data={
                "app_name": "Alice PA",
                "workspace_id": str(self.workspace.id),
                "environment_id": str(self.environment.id),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 302)
        owner_tag = ResourceTag.objects.filter(
            organization=self.org, resource_type="app",
            app__slug="alice-pa", key="owner",
        ).first()
        self.assertIsNotNone(owner_tag)
        self.assertEqual(owner_tag.value, "alice")
        app_type_tag = ResourceTag.objects.filter(
            organization=self.org, resource_type="app",
            app__slug="alice-pa", key="app-type",
        ).first()
        self.assertIsNotNone(app_type_tag)
        self.assertEqual(app_type_tag.value, "personal-assistant")
