"""Tests for the app-detail "Deployment Log" tab and its live-polling fragment."""

import uuid

from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    DeploymentLog,
    Environment,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.tests.app_test_factories import make_source_template

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestDeploymentLogTab(TestCase):
    """The Deployment Log tab is disabled until logs exist; its fragment shows the latest attempt's log."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Log Org", slug="log-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, source_template=make_source_template(),
            environment=self.env, name="MyApp", slug="myapp",
            container_port=8000, health_check_path="/health",
            cpu=256, memory=512, last_attempt_id=uuid.uuid7(),
        )

        self.admin_user = User.objects.create_user(username="log_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        self.client.force_login(self.admin_user)

    def _set_idle_deployed(self) -> None:
        self.app.job_status = App.JobStatus.IDLE
        self.app.live_state = App.LiveState.DEPLOYED
        self.app.save(update_fields=["job_status", "live_state", "updated_at"])

    def _set_deploying(self) -> None:
        self.app.job_status = App.JobStatus.DEPLOYING
        self.app.save(update_fields=["job_status", "updated_at"])

    def _log(self, attempt_id: uuid.UUID, source: str, level: str, message: str) -> None:
        DeploymentLog.objects.create(
            app=self.app, attempt_id=attempt_id, source=source, level=level, message=message,
        )

    def test_tab_disabled_when_no_logs(self) -> None:
        self._set_idle_deployed()
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Deployment Log", body)
        # Disabled: rendered as a non-clickable span, no fragment fetch wired up.
        self.assertIn("cursor-not-allowed", body)
        self.assertNotIn("/apps/myapp/deployment-log/", body)

    def test_tab_enabled_when_logs_exist(self) -> None:
        self._set_idle_deployed()
        self._log(self.app.last_attempt_id, "cdk", "info", "hello")
        response = self.client.get("/apps/myapp/", **HTMX)
        body = response.content.decode()
        self.assertNotIn("cursor-not-allowed", body)
        self.assertIn("/apps/myapp/deployment-log/", body)
        # Concluded attempt -> page opens on Overview, not the log.
        self.assertIn("{ tab: 'content' }", body)

    def test_in_progress_deploy_opens_log_tab_even_without_logs_yet(self) -> None:
        # A just-started deploy has no log lines yet, but the tab must be enabled and active.
        self._set_deploying()
        response = self.client.get("/apps/myapp/", **HTMX)
        body = response.content.decode()
        self.assertNotIn("cursor-not-allowed", body)
        self.assertIn("{ tab: 'logs' }", body)
        # The region self-loads the fragment on page load (no tab click happens).
        self.assertIn('hx-trigger="load"', body)

    def test_fragment_shows_latest_attempt_logs_colorized_and_polls_while_unsettled(self) -> None:
        old_attempt = uuid.uuid7()
        self._log(old_attempt, "app", "info", "OLD LINE")
        self._set_deploying()
        self._log(self.app.last_attempt_id, "cdk", "info", "NEW INFO LINE")
        self._log(self.app.last_attempt_id, "app", "error", "NEW ERROR LINE")

        response = self.client.get("/apps/myapp/deployment-log/", **HTMX)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Only the most recent attempt's logs.
        self.assertIn("NEW INFO LINE", body)
        self.assertIn("NEW ERROR LINE", body)
        self.assertNotIn("OLD LINE", body)
        # Colorized: error level gets the red class.
        self.assertIn("text-red-400", body)
        # Unsettled attempt -> self-polls every second.
        self.assertIn('hx-trigger="load delay:1s"', body)
        self.assertIn("Live", body)

    def test_fragment_stops_polling_when_concluded(self) -> None:
        self._set_idle_deployed()
        self._log(self.app.last_attempt_id, "app", "info", "done")
        response = self.client.get("/apps/myapp/deployment-log/", **HTMX)
        body = response.content.decode()
        self.assertIn("done", body)
        self.assertNotIn("hx-trigger", body)
