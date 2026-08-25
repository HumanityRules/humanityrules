"""Tests for the app-detail live regions and the status poll that updates them."""

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


class TestAppDetailLiveRegions(TestCase):
    """One poll endpoint drives the hero, the header actions, and History."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Live Org", slug="live-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
            status=Environment.Status.READY,
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.env, name="MyApp", slug="myapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512, last_attempt_id=uuid.uuid7(),
            live_state=App.LiveState.DEPLOYED, may_have_infra=True,
        )
        self.user = User.objects.create_user(username="live_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.user, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.user)
        self.client.force_login(self.user)

    def _record(self, event_type: str) -> None:
        DeploymentRecord.objects.create(
            app=self.app, attempt_id=self.app.last_attempt_id, event_type=event_type, created_by=self.user,
        )

    def _set_deploying(self) -> None:
        self.app.job_status = App.JobStatus.DEPLOYING
        self.app.save(update_fields=["job_status", "updated_at"])

    def _set_idle(self) -> None:
        """Back to idle, still deployed with live infra — the state removal is now offered from."""
        self.app.job_status = App.JobStatus.IDLE
        self.app.save(update_fields=["job_status", "updated_at"])

    def test_page_renders_both_regions_in_place_without_oob(self) -> None:
        self._record(DeploymentRecord.EventType.DEPLOY_STARTED)
        response = self.client.get("/apps/myapp/", **HTMX)

        body = response.content.decode()
        self.assertIn('id="app-detail-actions"', body)
        self.assertIn('id="deployment-history"', body)
        # OOB markers belong to poll responses only; on the page they would retarget the swap.
        self.assertNotIn("hx-swap-oob", body)

    def test_overview_tab_refetches_the_section(self) -> None:
        response = self.client.get("/apps/myapp/", **HTMX)

        body = response.content.decode()
        overview_button = body[body.index(">Overview<") - 900: body.index(">Overview<")]
        self.assertIn("/apps/myapp/deployment-section-status/", overview_button)
        self.assertIn(f'hx-target="#deployment-section-{self.app.id}"', overview_button)

    def test_poll_response_carries_live_regions_out_of_band(self) -> None:
        self._set_deploying()
        response = self.client.get("/apps/myapp/deployment-section-status/", **HTMX)

        body = response.content.decode()
        self.assertIn('id="app-removal-banner" hx-swap-oob="true"', body)
        self.assertIn('id="app-detail-actions" hx-swap-oob="true"', body)
        self.assertIn('id="deployment-history" hx-swap-oob="true"', body)
        # Still the live clock: the section keeps polling while the job runs.
        self.assertIn('hx-trigger="load delay:10s"', body)

    def test_poll_response_carries_events_logged_after_page_load(self) -> None:
        self._set_deploying()
        self._record(DeploymentRecord.EventType.DEPLOY_STARTED)
        self._record(DeploymentRecord.EventType.DEPLOY_SUCCEEDED)

        response = self.client.get("/apps/myapp/deployment-section-status/", **HTMX)

        body = response.content.decode()
        self.assertIn("Deploy Started", body)
        self.assertIn("Deploy Succeeded", body)

    def test_poll_response_offers_remove_once_the_app_becomes_removable(self) -> None:
        self._set_deploying()
        self.assertNotContains(self.client.get("/apps/myapp/deployment-section-status/", **HTMX), "Remove App")

        # A settled job leaves the app idle: removal is legal even though it is still deployed.
        self._set_idle()
        self.assertContains(self.client.get("/apps/myapp/deployment-section-status/", **HTMX), "Remove App")

    def test_poll_response_hides_remove_from_a_viewer(self) -> None:
        viewer = User.objects.create_user(username="live_viewer", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=viewer, role=OrganizationMembership.Role.MEMBER)
        IdentityAttribute.objects.create(organization=self.org, user=viewer, key="role", value="ws-viewer")
        ResourceTag.objects.create(
            organization=self.org, resource_type="workspace", workspace=self.workspace,
            key="domain", value="engineering",
        )
        Policy.objects.create(
            organization=self.org, name="WS viewer", resource_type="workspace",
            identity_conditions=[{"key": "role", "value": "ws-viewer"}],
            resource_conditions=[{"key": "domain", "value": "engineering"}],
            actions=["workspace:view"],
        )
        self._set_idle()
        self.client.force_login(viewer)

        response = self.client.get("/apps/myapp/deployment-section-status/", **HTMX)
        self.assertNotContains(response, "Remove App")
