"""Section 2: View-level endpoint access tests for ABAC authorization.

Integration tests using Django test client. Each test class sets up users with
different attribute profiles, hits ABAC-protected endpoints, and asserts 200 vs 403.

GET views with the HTMX early-return pattern skip the ABAC check for non-HTMX
requests (returning the app shell immediately). Tests use HTTP_HX_REQUEST to
trigger the content path where ABAC enforcement happens.
"""

import json

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    AppPermissionRequest,
    Deployment,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
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


# ---------------------------------------------------------------------------
# 2.1 Workspace Endpoints
# ---------------------------------------------------------------------------


class TestWorkspaceEndpoints(TestCase):
    """Workspace list, detail, create, and tag management access control."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="WS Test Org", slug="ws-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test Account")
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )

        self.ws_eng = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_eng,
            key="domain", value="engineering",
        )
        self.ws_fin = Workspace.objects.create(organization=self.org, name="Finance", slug="finance")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_fin,
            key="domain", value="finance",
        )

        self.admin_user = User.objects.create_user(username="ws_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.viewer_user = User.objects.create_user(username="ws_viewer", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.viewer_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.viewer_user, key="dept", value="engineering")
        Policy.objects.create(
            organization=self.org, name="Eng viewers", resource_type="workspace",
            identity_conditions=[{"key": "dept", "value": "engineering"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )

        self.editor_user = User.objects.create_user(username="ws_editor", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.editor_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.editor_user, key="role", value="editor")
        Policy.objects.create(
            organization=self.org, name="Eng editors", resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "editor"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view", "workspace:edit"],
        )

        self.no_access_user = User.objects.create_user(username="ws_noaccess", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.no_access_user, role=OrganizationMembership.Role.MEMBER)

        self.removable_tag = ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.ws_eng,
            key="removable", value="yes",
        )

    # --- Workspace List (filter_permitted_resources) ---

    def test_admin_workspace_list_shows_all(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/workspaces/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["workspaces"].values_list("pk", flat=True))
        self.assertIn(self.ws_eng.pk, pks)
        self.assertIn(self.ws_fin.pk, pks)

    def test_viewer_workspace_list_only_shows_permitted(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/workspaces/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["workspaces"].values_list("pk", flat=True))
        self.assertIn(self.ws_eng.pk, pks)
        self.assertNotIn(self.ws_fin.pk, pks)

    def test_no_access_workspace_list_is_empty(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/workspaces/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["workspaces"].count(), 0)

    # --- Workspace Detail ---

    def test_admin_can_view_workspace_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/workspaces/engineering/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_view_workspace_detail(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/workspaces/engineering/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_view_unmatched_workspace(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/workspaces/finance/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_workspace_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/workspaces/engineering/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Workspace Create ---

    def test_admin_can_create_workspace(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/workspaces/create/", {"name": "New WS"})
        self.assertEqual(response.status_code, 302)

    def test_no_access_gets_403_on_workspace_create(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.post("/workspaces/create/", {"name": "New WS"})
        self.assertEqual(response.status_code, 403)

    # --- Workspace Tag Management (requires workspace:admin) ---

    def test_admin_can_add_workspace_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/workspaces/engineering/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_workspace_tag_add(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post("/workspaces/engineering/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_editor_gets_403_on_workspace_tag_add(self) -> None:
        """workspace:edit is not enough — workspace:admin required for tag management."""
        self.client.force_login(self.editor_user)
        response = self.client.post("/workspaces/engineering/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_workspace_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/workspaces/engineering/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_workspace_tag_remove(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post(f"/workspaces/engineering/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# 2.2 Environment Endpoints
# ---------------------------------------------------------------------------


class TestEnvironmentEndpoints(TestCase):
    """Environment list, detail, and tag management access control."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Env Test Org", slug="env-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")

        self.env_staging = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_staging,
            key="tier", value="staging",
        )
        self.env_production = Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_production,
            key="tier", value="production",
        )

        self.admin_user = User.objects.create_user(username="env_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.viewer_user = User.objects.create_user(username="env_viewer", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.viewer_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.viewer_user, key="role", value="env-viewer")
        Policy.objects.create(
            organization=self.org, name="Staging viewers", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "env-viewer"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:view"],
        )

        self.env_admin_user = User.objects.create_user(username="env_envadmin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.env_admin_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.env_admin_user, key="role", value="env-admin")
        Policy.objects.create(
            organization=self.org, name="Staging admins", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "env-admin"}],
            resource_conditions=[{"key": "tier", "value": "staging"}],
            actions=["environment:admin"],
        )

        self.no_access_user = User.objects.create_user(username="env_noaccess", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.no_access_user, role=OrganizationMembership.Role.MEMBER)

        self.removable_tag = ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env_staging,
            key="removable", value="yes",
        )

    # --- Environment List (filter_permitted_resources) ---

    def test_admin_environment_list_shows_all(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["environments"].values_list("pk", flat=True))
        self.assertIn(self.env_staging.pk, pks)
        self.assertIn(self.env_production.pk, pks)

    def test_viewer_environment_list_only_shows_permitted(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = set(response.context["environments"].values_list("pk", flat=True))
        self.assertIn(self.env_staging.pk, pks)
        self.assertNotIn(self.env_production.pk, pks)

    # --- Environment Detail ---

    def test_admin_can_view_environment_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_view_environment_detail(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_view_unmatched_environment(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/environments/production/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_environment_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/environments/staging/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Environment Tag Management (requires environment:admin) ---

    def test_admin_can_add_environment_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_environment_tag_add(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 403)

    def test_env_admin_can_add_environment_tag(self) -> None:
        self.client.force_login(self.env_admin_user)
        response = self.client.post("/environments/staging/tags/add/", {"key": "env", "value": "test"})
        self.assertEqual(response.status_code, 200)

    def test_admin_can_remove_environment_tag(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/environments/staging/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 200)

    def test_viewer_gets_403_on_environment_tag_remove(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post(f"/environments/staging/tags/{self.removable_tag.id}/remove/")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# 2.3 App Endpoints (checked via parent workspace)
# ---------------------------------------------------------------------------


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

    # --- App Deployment Teardown (requires workspace:edit) ---

    def test_ws_editor_can_trigger_teardown(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.post(f"/apps/myapp/deployments/{self.deployment.id}/teardown/")
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_gets_403_on_teardown(self) -> None:
        self.client.force_login(self.ws_viewer)
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


# ---------------------------------------------------------------------------
# 2.4 Security Settings Endpoints (all require org admin)
# ---------------------------------------------------------------------------


class TestSecuritySettingsEndpoints(TestCase):
    """Every security settings endpoint requires org admin. Non-admins get 403."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Sec Test Org", slug="sec-test-org")

        self.admin_user = User.objects.create_user(username="sec_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

        self.regular_user = User.objects.create_user(username="sec_regular", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.regular_user, role=OrganizationMembership.Role.MEMBER)

        self.group = Group.objects.create(organization=self.org, name="Test Group")
        self.group_attr = GroupAttribute.objects.create(group=self.group, key="team", value="test")
        self.group_membership = GroupMembership.objects.create(group=self.group, user=self.regular_user)

        self.identity_attr = IdentityAttribute.objects.create(
            organization=self.org, user=self.regular_user, key="test-attr", value="test-val",
        )

        self.policy = Policy.objects.create(
            organization=self.org, name="Test Policy", resource_type="workspace",
            identity_conditions=[{"key": "test", "value": "test"}],
            resource_conditions=[{"key": "test", "value": "test"}],
            actions=["workspace:view"],
        )

    # --- People List & Detail ---

    def test_admin_can_access_people_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/people/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_people_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/people/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_people_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/people/{self.regular_user.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_people_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/people/{self.regular_user.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- People Attribute Mutations ---

    def test_admin_can_add_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/add/",
            {"key": "new", "value": "val"},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/add/",
            {"key": "new", "value": "val"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/{self.identity_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/attributes/{self.identity_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- People Group Membership Mutations ---

    def test_admin_can_add_person_to_group(self) -> None:
        new_group = Group.objects.create(organization=self.org, name="New Group")
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/add/",
            {"group_id": str(new_group.id)},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_person_to_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/add/",
            {"group_id": str(self.group.id)},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_person_from_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_person_from_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/people/{self.regular_user.id}/groups/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Groups List, Create, Detail, Delete ---

    def test_admin_can_access_groups_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/groups/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_groups_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/groups/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/security/groups/create/", {"name": "New Group 2"})
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_create_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post("/security/groups/create/", {"name": "New Group 2"})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_group_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/groups/{self.group.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_group_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/groups/{self.group.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_delete_group(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/security/groups/{self.group.id}/delete/")
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_delete_group(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(f"/security/groups/{self.group.id}/delete/")
        self.assertEqual(response.status_code, 403)

    # --- Group Attribute Mutations ---

    def test_admin_can_add_group_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/add/",
            {"key": "new-key", "value": "new-val"},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_group_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/add/",
            {"key": "new-key", "value": "new-val"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_group_attribute(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/{self.group_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_group_attribute(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/attributes/{self.group_attr.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Group Member Mutations ---

    def test_admin_can_add_group_member(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/add/",
            {"user_id": str(self.admin_user.id)},
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_add_group_member(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/add/",
            {"user_id": str(self.admin_user.id)},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_remove_group_member(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_remove_group_member(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(
            f"/security/groups/{self.group.id}/members/{self.group_membership.id}/remove/",
        )
        self.assertEqual(response.status_code, 403)

    # --- Policies List, Create, Detail, Delete ---

    def test_admin_can_access_policies_list(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/security/policies/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_policies_list(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get("/security/policies/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_policy(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/security/policies/create/", {
            "name": "New Policy",
            "resource_type": "workspace",
            "identity_conditions": json.dumps([{"key": "role", "value": "dev"}]),
            "resource_conditions": json.dumps([{"key": "domain", "value": "eng"}]),
            "actions": json.dumps(["workspace:view"]),
        })
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_create_policy(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post("/security/policies/create/", {
            "name": "New Policy",
            "resource_type": "workspace",
            "identity_conditions": json.dumps([{"key": "role", "value": "dev"}]),
            "resource_conditions": json.dumps([{"key": "domain", "value": "eng"}]),
            "actions": json.dumps(["workspace:view"]),
        })
        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_policy_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"/security/policies/{self.policy.id}/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_non_admin_gets_403_on_policy_detail(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.get(f"/security/policies/{self.policy.id}/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_delete_policy(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(f"/security/policies/{self.policy.id}/delete/")
        self.assertEqual(response.status_code, 302)

    def test_non_admin_gets_403_on_delete_policy(self) -> None:
        self.client.force_login(self.regular_user)
        response = self.client.post(f"/security/policies/{self.policy.id}/delete/")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# 2.5 Permissions Editor (environment:approve on target environment)
# ---------------------------------------------------------------------------


class TestPermissionsEditorEndpoints(TestCase):
    """Approving an AppPermissionRequest requires environment:approve."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="PE Test Org", slug="pe-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )

        self.workspace = Workspace.objects.create(organization=self.org, name="WS", slug="ws")
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            name="PEApp", slug="peapp", app_type="web", build_strategy="dockerfile",
            branch="main", container_port=8000, cpu=256, memory=512,
            health_check_path="/health",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production", aws_region="us-east-1",
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type="environment", environment=self.env,
            key="tier", value="production",
        )

        self.apr = AppPermissionRequest.objects.create(
            app=self.app, environment=self.env,
            statements=[{"effect": "Allow", "action": ["s3:GetObject"], "resource": ["*"]}],
            status=AppPermissionRequest.Status.DRAFT,
        )

        self.approver_user = User.objects.create_user(username="pe_approver", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.approver_user, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=self.approver_user, key="role", value="approver")
        Policy.objects.create(
            organization=self.org, name="Prod approvers", resource_type="environment",
            identity_conditions=[{"key": "role", "value": "approver"}],
            resource_conditions=[{"key": "tier", "value": "production"}],
            actions=["environment:approve"],
        )

        self.non_approver_user = User.objects.create_user(username="pe_nonapprover", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.non_approver_user, role=OrganizationMembership.Role.MEMBER)

    def test_approver_can_approve(self) -> None:
        self.client.force_login(self.approver_user)
        response = self.client.post(f"/security/permissions/{self.apr.id}/apply/")
        self.assertEqual(response.status_code, 200)

    def test_non_approver_gets_403_on_approve(self) -> None:
        self.client.force_login(self.non_approver_user)
        response = self.client.post(f"/security/permissions/{self.apr.id}/apply/")
        self.assertEqual(response.status_code, 403)
