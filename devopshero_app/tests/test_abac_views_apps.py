"""ABAC view tests: App endpoints.

App detail, deployment ops (status polling, teardown, redeploy),
and tag management — access derived from parent workspace.
"""

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Deployment,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from devopshero_app.services import abac

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestAppEndpoints(TestCase):
    """App detail, deployment ops, and tag management — access derived from parent workspace."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="App Test Org", slug="app-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )

        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )

        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name="MyApp", slug="myapp", app_type="web", build_strategy="dockerfile",
            branch="main", container_port=8000, cpu=256, memory=512,
            health_check_path="/health",
        )

        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        self.deployment = Deployment.objects.create(
            app=self.app, environment=self.env,
            subdomain="myapp-staging", git_ref="main", image_tag="myapp-main-20260227",
            status=Deployment.Status.DEPLOYED, status_message="Running",
        )

        self.admin_user = User.objects.create_user(username="app_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.ws_viewer = User.objects.create_user(username="app_ws_viewer", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.ws_viewer, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.ws_viewer, key="role", value="ws-viewer")
        Policy.objects.create(
            organization=self.org, name="WS viewer", resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "ws-viewer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )

        self.ws_editor = User.objects.create_user(username="app_ws_editor", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.ws_editor, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.ws_editor, key="role", value="ws-editor")
        Policy.objects.create(
            organization=self.org, name="WS editor", resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "ws-editor"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view", "workspace:edit"],
        )

        self.ws_admin = User.objects.create_user(username="app_ws_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.ws_admin, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.ws_admin, key="role", value="ws-admin")
        Policy.objects.create(
            organization=self.org, name="WS admin", resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "ws-admin"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:admin"],
        )

        self.no_access_user = User.objects.create_user(username="app_noaccess", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.no_access_user, role=OrganizationMembership.Role.MEMBER)

        self.app_tag = ResourceTag.objects.create(
            organization=self.org, resource_type="app", app=self.app,
            key="removable", value="yes",
        )

    # --- App Detail (requires workspace:view on parent workspace) ---

    def test_admin_can_view_app_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_can_view_app_detail(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_no_access_gets_403_on_app_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- App Deployment Status Polling (requires workspace:view) ---

    def test_ws_viewer_can_poll_deployment_status(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get(f"/apps/myapp/deployments/{self.deployment.id}/status/")
        self.assertEqual(response.status_code, 200)

    def test_no_access_gets_403_on_deployment_status(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get(f"/apps/myapp/deployments/{self.deployment.id}/status/")
        self.assertEqual(response.status_code, 403)

    # --- App Teardown Confirm Modal (requires workspace:view) ---

    def test_ws_viewer_can_fetch_teardown_confirm(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get(f"/apps/myapp/deployments/{self.deployment.id}/teardown-confirm/")
        self.assertEqual(response.status_code, 200)

    def test_no_access_gets_403_on_teardown_confirm(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get(f"/apps/myapp/deployments/{self.deployment.id}/teardown-confirm/")
        self.assertEqual(response.status_code, 403)

    # --- App Deployment Teardown (requires workspace:edit) ---

    def test_ws_editor_can_trigger_teardown(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/teardown/")
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_gets_403_on_teardown(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/teardown/")
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_teardown(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/teardown/")
        self.assertEqual(response.status_code, 403)

    # --- App Deployment Redeploy (requires workspace:edit) ---

    def test_ws_editor_can_trigger_redeploy(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/redeploy/")
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_gets_403_on_redeploy(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/redeploy/")
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_redeploy(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/redeploy/")
        self.assertEqual(response.status_code, 403)

    # --- App Tag Management (requires workspace:admin on parent workspace) ---

    def test_ws_admin_can_add_app_tag(self) -> None:
        self.client.force_login(self.ws_admin)
        response = self.client.post("/apps/myapp/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_gets_403_on_app_tag_add(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.post("/apps/myapp/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_ws_editor_gets_403_on_app_tag_add(self) -> None:
        """workspace:edit is not enough — workspace:admin required for tag management."""
        self.client.force_login(self.ws_editor)
        response = self.client.post("/apps/myapp/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_ws_admin_can_remove_app_tag(self) -> None:
        self.client.force_login(self.ws_admin)
        response = self.client.post(f"/apps/myapp/tags/{self.app_tag.id}/remove/")
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_gets_403_on_app_tag_remove(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.post(f"/apps/myapp/tags/{self.app_tag.id}/remove/")
        self.assertEqual(response.status_code, 403)

    def test_ws_editor_gets_403_on_app_tag_remove(self) -> None:
        """workspace:edit is not enough — workspace:admin required for tag management."""
        self.client.force_login(self.ws_editor)
        response = self.client.post(f"/apps/myapp/tags/{self.app_tag.id}/remove/")
        self.assertEqual(response.status_code, 403)
