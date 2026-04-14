"""ABAC view tests: Workspace endpoints.

Workspace list, detail, create, and tag management access control.
"""

from django.test import TestCase

from devopshero_app.models import (
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
    Workspace,
)
from devopshero_app.services import abac

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestWorkspaceEndpoints(TestCase):
    """Workspace list, detail, create, and tag management access control."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="WS Test Org", slug="ws-test-org")

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

    def test_admin_can_bulk_save_workspace_tags(self) -> None:
        import json
        self.client.force_login(self.admin_user)
        response = self.client.post(
            "/workspaces/engineering/tags/save/",
            {"tags": json.dumps([{"key": "env", "value": "prod"}, {"key": "team", "value": "alpha"}])},
        )
        self.assertEqual(response.status_code, 200)
        tags = ResourceTag.objects.filter(workspace=self.ws_eng).order_by("key")
        self.assertEqual(tags.count(), 2)
        self.assertEqual(tags[0].key, "env")

    def test_viewer_gets_403_on_workspace_tags_save(self) -> None:
        import json
        self.client.force_login(self.viewer_user)
        response = self.client.post(
            "/workspaces/engineering/tags/save/",
            {"tags": json.dumps([{"key": "env", "value": "prod"}])},
        )
        self.assertEqual(response.status_code, 403)
