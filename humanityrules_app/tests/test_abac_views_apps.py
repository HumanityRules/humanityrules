"""ABAC view tests: App endpoints.

App detail, deployment ops (status polling, redeploy),
and tag management — access derived from parent workspace.
"""

import uuid

from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    DeploymentRecord,
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


class TestAppEndpoints(TestCase):
    """App detail, deployment ops, and tag management — access derived from parent workspace."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="App Test Org", slug="app-test-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")

        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )

        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            status=Environment.Status.READY,
        )
        # A live, idle app: deployed with infra behind it, so removal must tear it down.
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.env, name="MyApp", slug="myapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
            live_state=App.LiveState.DEPLOYED, may_have_infra=True,
            service_url="https://myapp.staging.example.com", last_attempt_id=uuid.uuid7(),
        )

        self.admin_user = User.objects.create_user(username="app_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)

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

    # --- App Detail (requires workspace:view on parent workspace) ---

    def test_admin_can_view_app_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_can_view_app_detail(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_app_detail_offers_remove_while_deployed(self) -> None:
        """Removal no longer waits on a teardown: a live app is removable as long as it is idle."""
        self.client.force_login(self.ws_editor)

        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remove App")
        self.assertContains(response, "/apps/myapp/remove-confirm/")

    def test_app_detail_offers_no_tear_down(self) -> None:
        """Tear Down is gone from the product: removal is the one destructive action."""
        self.client.force_login(self.ws_editor)

        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Tear Down")

    def test_app_detail_polls_section_while_a_job_is_in_flight(self) -> None:
        self.app.job_status = App.JobStatus.DEPLOY_PENDING
        self.app.save(update_fields=["job_status", "updated_at"])
        self.client.force_login(self.ws_editor)

        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/apps/myapp/deployment-section-status/")
        self.assertContains(response, 'hx-trigger="load delay:10s"')
        # No redeploy control while a deploy is running.
        self.assertNotContains(response, "/apps/myapp/redeploy/")

    def test_no_access_gets_403_on_app_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- App Status Row Polling (requires workspace:view) ---

    def test_ws_viewer_can_poll_status_row(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/status-row/")
        self.assertEqual(response.status_code, 200)

    def test_no_access_gets_403_on_status_row(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/apps/myapp/status-row/")
        self.assertEqual(response.status_code, 403)

    # --- App Deployment Section Status Polling (requires workspace:view) ---

    def test_ws_viewer_can_poll_deployment_section_status(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/deployment-section-status/")
        self.assertEqual(response.status_code, 200)

    def test_no_access_gets_403_on_deployment_section_status(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/apps/myapp/deployment-section-status/")
        self.assertEqual(response.status_code, 403)

    # --- Tear Down endpoints are gone ---

    def test_teardown_endpoints_are_gone(self) -> None:
        self.client.force_login(self.ws_editor)
        self.assertEqual(self.client.get("/apps/myapp/teardown-confirm/").status_code, 404)
        self.assertEqual(self.client.post("/apps/myapp/teardown/").status_code, 404)

    # --- App Deployment Redeploy (requires workspace:edit) ---

    def test_ws_editor_can_trigger_redeploy(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.post("/apps/myapp/redeploy/")
        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, App.JobStatus.DEPLOY_PENDING)

    def test_redeploy_is_blocked_while_a_job_is_in_flight(self) -> None:
        self.app.job_status = App.JobStatus.REMOVAL_PENDING
        self.app.save(update_fields=["job_status", "updated_at"])
        self.client.force_login(self.ws_editor)

        response = self.client.post("/apps/myapp/redeploy/")

        self.assertEqual(response.status_code, 422)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, App.JobStatus.REMOVAL_PENDING)
        self.assertFalse(
            DeploymentRecord.objects.filter(
                app=self.app, event_type=DeploymentRecord.EventType.DEPLOY_STARTED,
            ).exists()
        )

    def test_ws_viewer_gets_403_on_redeploy(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.post("/apps/myapp/redeploy/")
        self.assertEqual(response.status_code, 403)

    def test_no_access_gets_403_on_redeploy(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.post("/apps/myapp/redeploy/")
        self.assertEqual(response.status_code, 403)

    # --- App Tag Management (requires workspace:admin on parent workspace) ---

    def test_ws_admin_can_bulk_save_app_tags(self) -> None:
        import json
        self.client.force_login(self.ws_admin)
        response = self.client.post(
            "/apps/myapp/tags/save/",
            {"tags": json.dumps([{"key": "env", "value": "prod"}])},
        )
        self.assertEqual(response.status_code, 200)
        tags = ResourceTag.objects.filter(app=self.app)
        self.assertEqual(tags.count(), 1)
        self.assertEqual(tags[0].key, "env")

    def test_ws_viewer_gets_403_on_app_tags_save(self) -> None:
        import json
        self.client.force_login(self.ws_viewer)
        response = self.client.post(
            "/apps/myapp/tags/save/",
            {"tags": json.dumps([{"key": "env", "value": "prod"}])},
        )
        self.assertEqual(response.status_code, 403)
