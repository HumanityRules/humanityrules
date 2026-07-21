"""Tests for the app-detail "Deployment Log" tab and its live-polling fragment."""

from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    Deployment,
    DeploymentLog,
    Environment,
    Organization,
    OrganizationMembership,
    Repository,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service

HTMX = {"HTTP_HX_REQUEST": "true"}


class TestDeploymentLogTab(TestCase):
    """The Deployment Log tab is disabled until logs exist; its fragment shows the latest deployment's log."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Log Org", slug="log-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Test AWS")
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="repo",
            full_name="org/repo", clone_url="https://github.com/org/repo.git",
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            environment=self.env, name="MyApp", slug="myapp", app_type="web",
            build_strategy="dockerfile", container_port=8000, health_check_path="/health",
            cpu=256, memory=512,
        )

        self.admin_user = User.objects.create_user(username="log_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin_user, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        self.client.force_login(self.admin_user)

    def _make_deployment(self, status: str) -> Deployment:
        return Deployment.objects.create(
            app=self.app, git_ref="main", image_tag="myapp-main-1", status=status,
        )

    def test_tab_disabled_when_no_logs(self) -> None:
        self._make_deployment(Deployment.Status.SUCCEEDED)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Deployment Log", body)
        # Disabled: rendered as a non-clickable span, no fragment fetch wired up.
        self.assertIn("cursor-not-allowed", body)
        self.assertNotIn("/apps/myapp/deployment-log/", body)

    def test_tab_enabled_when_logs_exist(self) -> None:
        deployment = self._make_deployment(Deployment.Status.SUCCEEDED)
        DeploymentLog.objects.create(deployment=deployment, source="cdk", level="info", message="hello")
        response = self.client.get("/apps/myapp/", **HTMX)
        body = response.content.decode()
        self.assertNotIn("cursor-not-allowed", body)
        self.assertIn("/apps/myapp/deployment-log/", body)
        # Concluded deployment -> page opens on Overview, not the log.
        self.assertIn("{ tab: 'content' }", body)

    def test_in_progress_deploy_opens_log_tab_even_without_logs_yet(self) -> None:
        # A just-started deploy has no log lines yet, but the tab must be enabled and active.
        self._make_deployment(Deployment.Status.DEPLOYING)
        response = self.client.get("/apps/myapp/", **HTMX)
        body = response.content.decode()
        self.assertNotIn("cursor-not-allowed", body)
        self.assertIn("{ tab: 'logs' }", body)
        # The region self-loads the fragment on page load (no tab click happens).
        self.assertIn('hx-trigger="load"', body)

    def test_fragment_shows_latest_deployment_logs_colorized_and_polls_while_unsettled(self) -> None:
        old = self._make_deployment(Deployment.Status.SUCCEEDED)
        DeploymentLog.objects.create(deployment=old, source="app", level="info", message="OLD LINE")
        new = self._make_deployment(Deployment.Status.DEPLOYING)
        DeploymentLog.objects.create(deployment=new, source="cdk", level="info", message="NEW INFO LINE")
        DeploymentLog.objects.create(deployment=new, source="app", level="error", message="NEW ERROR LINE")

        response = self.client.get("/apps/myapp/deployment-log/", **HTMX)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Only the most recent deployment's logs.
        self.assertIn("NEW INFO LINE", body)
        self.assertIn("NEW ERROR LINE", body)
        self.assertNotIn("OLD LINE", body)
        # Colorized: error level gets the red class.
        self.assertIn("text-red-400", body)
        # Unsettled deployment -> self-polls every second.
        self.assertIn('hx-trigger="load delay:1s"', body)
        self.assertIn("Live", body)

    def test_fragment_stops_polling_when_concluded(self) -> None:
        deployment = self._make_deployment(Deployment.Status.SUCCEEDED)
        DeploymentLog.objects.create(deployment=deployment, source="app", level="info", message="done")
        response = self.client.get("/apps/myapp/deployment-log/", **HTMX)
        body = response.content.decode()
        self.assertIn("done", body)
        self.assertNotIn("hx-trigger", body)
