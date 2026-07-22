"""ABAC view tests: Workspace endpoints.

Workspace list, detail, create, and tag management access control.
"""

from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    AppTemplate,
    Environment,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.tests.app_test_factories import make_source_template

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestWorkspaceEndpoints(TestCase):
    """Workspace list, detail, create, and tag management access control."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="WS Test Org", slug="ws-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
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
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

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

    # --- Dashboard workspace sections (filter_permitted_resources) ---

    def test_admin_dashboard_shows_default_first_then_workspaces_alphabetically(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/dashboard/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = {workspace.pk for workspace in response.context["workspaces"]}
        self.assertIn(self.ws_eng.pk, pks)
        self.assertIn(self.ws_fin.pk, pks)
        self.assertEqual(
            [workspace.slug for workspace in response.context["workspaces"]],
            ["default", "engineering", "finance"],
        )

    def test_viewer_dashboard_only_shows_permitted_workspaces(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/dashboard/", **HTMX)
        self.assertEqual(response.status_code, 200)
        pks = {workspace.pk for workspace in response.context["workspaces"]}
        self.assertIn(self.ws_eng.pk, pks)
        self.assertNotIn(self.ws_fin.pk, pks)

    def test_no_access_dashboard_workspace_list_is_empty(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/dashboard/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["workspaces"], [])

    def test_dashboard_hides_default_title_and_links_other_workspace_titles(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/dashboard/", **HTMX)

        self.assertContains(response, 'data-workspace-section="default"')
        self.assertNotContains(response, 'data-workspace-title="default"')
        self.assertContains(response, 'data-workspace-title="engineering"')
        self.assertContains(response, 'href="/workspaces/engineering/"')

    def test_dashboard_shows_create_workspace_and_admin_action_menus(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/dashboard/", **HTMX)

        self.assertContains(response, "Create Workspace")
        self.assertContains(response, 'aria-label="Open actions for Default"')
        self.assertContains(response, 'aria-label="Open actions for Engineering"')
        self.assertContains(response, "Delete Workspace")

    def test_dashboard_shell_navigation_omits_workspaces(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/dashboard/")

        navigation_names = [item["name"] for item in response.context["navigation_items"]]
        self.assertIn("Dashboard", navigation_names)
        self.assertNotIn("Workspaces", navigation_names)

    def test_dashboard_new_agent_card_carries_workspace_selection(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/dashboard/", **HTMX)

        deploy_url = f'/deploy/from-template/hermes-personal/?workspace_id={self.ws_eng.id}'
        self.assertContains(response, deploy_url, count=2)

    def test_template_deploy_form_uses_requested_permitted_workspace(self) -> None:
        template = AppTemplate.objects.create(
            name="Hermes Personal",
            slug="hermes-personal",
            description="Personal agent",
            icon="H",
            category="ai-assistant",
            cpu=256,
            memory=512,
            containers=[],
            is_active=True,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            f"/deploy/from-template/{template.slug}/?workspace_id={self.ws_eng.id}",
            **HTMX,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_workspace_id"], str(self.ws_eng.id))
        self.assertEqual(response.context["selected_workspace_label"], self.ws_eng.name)

    def test_template_deploy_shell_preserves_requested_workspace(self) -> None:
        template = AppTemplate.objects.create(
            name="Hermes Personal",
            slug="hermes-personal",
            description="Personal agent",
            icon="H",
            category="ai-assistant",
            cpu=256,
            memory=512,
            containers=[],
            is_active=True,
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            f"/deploy/from-template/{template.slug}/?workspace_id={self.ws_eng.id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["content_url"],
            f"/deploy/from-template/{template.slug}/?workspace_id={self.ws_eng.id}",
        )

    def test_legacy_workspace_list_route_is_gone(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/workspaces/", **HTMX)
        self.assertEqual(response.status_code, 404)

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

    def test_blank_workspace_name_redirects_to_dashboard(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post("/workspaces/create/", {"name": ""})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_no_access_gets_403_on_workspace_create(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.post("/workspaces/create/", {"name": "New WS"})
        self.assertEqual(response.status_code, 403)

    # --- Workspace Delete (requires workspace:admin; blocked when non-empty) ---

    def test_admin_can_get_workspace_remove_confirm(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/workspaces/engineering/remove-confirm/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_empty"])

    def test_viewer_gets_403_on_workspace_remove_confirm(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.get("/workspaces/engineering/remove-confirm/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_editor_gets_403_on_workspace_remove(self) -> None:
        """workspace:edit is not enough — workspace:admin is required to delete."""
        self.client.force_login(self.editor_user)
        response = self.client.post("/workspaces/engineering/remove/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Workspace.objects.filter(pk=self.ws_eng.pk).exists())

    def test_admin_can_delete_empty_workspace(self) -> None:
        self.client.force_login(self.admin_user)
        ws = Workspace.objects.create(organization=self.org, name="Disposable", slug="disposable")
        response = self.client.post("/workspaces/disposable/remove/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/dashboard/")
        self.assertFalse(Workspace.objects.filter(pk=ws.pk).exists())

    def test_admin_cannot_delete_workspace_with_app(self) -> None:
        self.client.force_login(self.admin_user)
        App.objects.create(
            organization=self.org, workspace=self.ws_eng, source_template=make_source_template(),
            environment=self.env, name="MyApp", slug="myapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )
        response = self.client.post("/workspaces/engineering/remove/")
        self.assertEqual(response.status_code, 422)
        self.assertTrue(Workspace.objects.filter(pk=self.ws_eng.pk).exists())

    def test_remove_confirm_reports_non_empty(self) -> None:
        self.client.force_login(self.admin_user)
        App.objects.create(
            organization=self.org, workspace=self.ws_fin, source_template=make_source_template(),
            environment=self.env, name="FinApp", slug="finapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )
        response = self.client.get("/workspaces/finance/remove-confirm/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_empty"])
        self.assertEqual(response.context["app_count"], 1)

    def test_viewer_gets_403_on_workspace_remove(self) -> None:
        self.client.force_login(self.viewer_user)
        response = self.client.post("/workspaces/engineering/remove/")
        self.assertEqual(response.status_code, 403)

    # --- Workspace Tag Management (requires workspace:admin) ---

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
