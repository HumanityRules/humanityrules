"""ABAC view tests: App endpoints.

App detail, deployment ops (status polling, teardown, redeploy),
and tag management — access derived from parent workspace.
"""

from django.test import TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Conversation,
    Deployment,
    DeploymentBlueprint,
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
            branch="main", container_port=8000, health_check_path="/health",
        )

        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        self.blueprint = DeploymentBlueprint.objects.create(
            app=self.app, environment=self.env, status=DeploymentBlueprint.Status.ACTIVE,
            cpu=256, memory=512, subdomain="myapp-staging", created_by=None,
        )
        self.deployment = Deployment.objects.create(
            blueprint=self.blueprint, app=self.app, environment=self.env,
            subdomain="myapp-staging", git_ref="main", image_tag="myapp-main-20260227",
            status=Deployment.Status.SUCCEEDED, status_message="Running",
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

    def _set_open_blueprint_status(self, status: str) -> None:
        self.deployment.delete()
        self.blueprint.status = status
        self.blueprint.save(update_fields=["status", "updated_at"])

    # --- App Detail (requires workspace:view on parent workspace) ---

    def test_admin_can_view_app_detail(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_ws_viewer_can_view_app_detail(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)

    def test_ws_editor_sees_deployment_button(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deployment")
        self.assertContains(response, "/deploy/myapp/")
        self.assertNotContains(response, 'hx-trigger="load delay:10s"')

    def test_app_detail_deployed_environments_uses_blueprints_outside_recent_deployments_window(self) -> None:
        legacy_env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Legacy",
            slug="legacy",
            aws_region="us-east-1",
        )
        legacy_blueprint = DeploymentBlueprint.objects.create(
            app=self.app,
            environment=legacy_env,
            status=DeploymentBlueprint.Status.ACTIVE,
            cpu=256,
            memory=512,
            subdomain="myapp-legacy",
            created_by=None,
        )
        Deployment.objects.create(
            blueprint=legacy_blueprint,
            app=self.app,
            environment=legacy_env,
            subdomain="myapp-legacy",
            git_ref="main",
            image_tag="myapp-legacy-20260311",
            status=Deployment.Status.SUCCEEDED,
            status_message="Running",
        )
        for index in range(25):
            Deployment.objects.create(
                blueprint=self.blueprint,
                app=self.app,
                environment=self.env,
                subdomain="myapp-staging",
                git_ref="main",
                image_tag=f"myapp-main-{index}",
                status=Deployment.Status.SUCCEEDED,
                status_message="Running",
            )

        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        row_blueprint_ids = {row.blueprint.id for row in response.context["environment_rows"]}
        self.assertIn(self.blueprint.id, row_blueprint_ids)
        self.assertIn(legacy_blueprint.id, row_blueprint_ids)
        self.assertEqual(len(response.context["deployments"]), 20)
        self.assertContains(response, "Legacy")

    def test_app_detail_deployed_environments_excludes_unlaunched_blueprints(self) -> None:
        preview_env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Preview",
            slug="preview",
            aws_region="us-east-1",
        )
        preview_blueprint = DeploymentBlueprint.objects.create(
            app=self.app,
            environment=preview_env,
            status=DeploymentBlueprint.Status.DRAFT,
            cpu=256,
            memory=512,
            subdomain="myapp-preview",
            created_by=None,
        )

        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        row_blueprint_ids = {row.blueprint.id for row in response.context["environment_rows"]}
        self.assertIn(self.blueprint.id, row_blueprint_ids)
        self.assertNotIn(preview_blueprint.id, row_blueprint_ids)

    def test_app_detail_deployed_environments_uses_latest_launched_blueprint_per_environment(self) -> None:
        newer_blueprint = DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.env,
            status=DeploymentBlueprint.Status.FAILED,
            cpu=256,
            memory=512,
            subdomain="myapp-staging-v2",
            created_by=None,
        )
        Deployment.objects.create(
            blueprint=newer_blueprint,
            app=self.app,
            environment=self.env,
            subdomain="myapp-staging-v2",
            git_ref="release",
            image_tag="myapp-release-1",
            status=Deployment.Status.SUCCEEDED,
            status_message="Running",
        )
        failed_attempt = Deployment.objects.create(
            blueprint=newer_blueprint,
            app=self.app,
            environment=self.env,
            subdomain="myapp-staging-v2",
            git_ref="release",
            image_tag="myapp-release-2",
            status=Deployment.Status.FAILED,
            status_message="Rollback required",
        )

        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["environment_rows"]), 1)
        row = response.context["environment_rows"][0]
        self.assertEqual(row.blueprint.id, newer_blueprint.id)
        self.assertNotEqual(row.current_deployment.id, failed_attempt.id)
        self.assertEqual(row.current_deployment.status, Deployment.Status.SUCCEEDED)

    def test_app_detail_deployed_environments_prefers_in_progress_redeploy_on_current_blueprint(self) -> None:
        redeploy_attempt = Deployment.objects.create(
            blueprint=self.blueprint,
            app=self.app,
            environment=self.env,
            subdomain="myapp-staging",
            git_ref="main",
            image_tag="myapp-main-20260311-redeploy",
            status=Deployment.Status.PENDING,
            status_message="Queued for redeploy",
        )

        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["environment_rows"]), 1)
        row = response.context["environment_rows"][0]
        self.assertEqual(row.blueprint.id, self.blueprint.id)
        self.assertEqual(row.current_deployment.id, redeploy_attempt.id)
        self.assertEqual(row.current_deployment.status, Deployment.Status.PENDING)
        self.assertNotContains(response, "Redeploy")
        self.assertContains(response, f"/blueprints/{self.blueprint.id}/row-status/")
        self.assertContains(response, 'hx-trigger="load delay:10s"')

    def test_app_detail_shows_teardown_in_deployed_environments_not_recent_deployments(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.get("/apps/myapp/", **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tear Down", count=1)
        self.assertContains(response, "?render=app_detail")

    def test_ws_viewer_does_not_see_deployment_button(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/deploy/myapp/")

    def test_no_access_gets_403_on_app_detail(self) -> None:
        self.client.force_login(self.no_access_user)
        response = self.client.get("/apps/myapp/", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Deployment Editor Entry Points (requires workspace:edit) ---

    def test_ws_editor_deployment_editor_creates_conversation_without_blueprint(self) -> None:
        self.client.force_login(self.ws_editor)
        response = self.client.get("/deploy/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        conversation = response.context["conversation"]
        self.assertEqual(conversation.context_app_id, self.app.id)
        self.assertIsNone(conversation.context_deployment_blueprint_id)
        self.assertIsNone(response.context["blueprint"])

    def test_ws_editor_deployment_editor_reuses_blueprint_conversation(self) -> None:
        self._set_open_blueprint_status(status=DeploymentBlueprint.Status.DRAFT)
        conversation = Conversation.objects.create(
            user=self.ws_editor,
            organization=self.org,
            context_workspace=self.workspace,
            context_repository=self.repo,
            context_app=self.app,
            context_deployment_blueprint=self.blueprint,
            mode=Conversation.Mode.APP_DEPLOYMENT,
            status=Conversation.Status.COMPLETED,
        )
        self.client.force_login(self.ws_editor)
        response = self.client.get("/deploy/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["conversation"].id, conversation.id)
        self.assertEqual(response.context["blueprint"].id, self.blueprint.id)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.Status.ACTIVE)

    def test_ws_editor_deployment_editor_shows_effective_branch_and_url_values(self) -> None:
        self.repo.default_branch = "master"
        self.repo.save()
        self.env.shared_alb_hosted_zone = "example.com"
        self.env.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self.blueprint.branch = ""
        self.blueprint.subdomain = ""
        self.blueprint.save()
        self._set_open_blueprint_status(status=DeploymentBlueprint.Status.DRAFT)
        self.client.force_login(self.ws_editor)
        response = self.client.get("/deploy/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        effective_values = response.context["blueprint_effective_values"]
        self.assertEqual(effective_values.branch, "master")
        self.assertEqual(effective_values.cpu_display, "256 units (0.25 vCPU)")
        self.assertEqual(effective_values.subdomain, "myapp")
        self.assertEqual(effective_values.url, "https://myapp.example.com")
        self.assertContains(response, "master")
        self.assertContains(response, "256 units (0.25 vCPU)")
        self.assertContains(response, "https://myapp.example.com")
        self.assertContains(response, "myapp")
        self.assertNotContains(response, "Inherited from repository default branch")
        self.assertNotContains(response, "Inherited from app slug")

    def test_ws_editor_deployment_editor_shows_explicit_url_value(self) -> None:
        self.env.shared_alb_hosted_zone = "example.com"
        self.env.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self.blueprint.branch = "release-2026"
        self.blueprint.subdomain = "preview-myapp"
        self.blueprint.save()
        self._set_open_blueprint_status(status=DeploymentBlueprint.Status.DRAFT)
        self.client.force_login(self.ws_editor)
        response = self.client.get("/deploy/myapp/", **HTMX)
        self.assertEqual(response.status_code, 200)
        effective_values = response.context["blueprint_effective_values"]
        self.assertEqual(effective_values.branch, "release-2026")
        self.assertEqual(effective_values.subdomain, "preview-myapp")
        self.assertEqual(effective_values.url, "https://preview-myapp.example.com")
        self.assertContains(response, "release-2026")
        self.assertContains(response, "https://preview-myapp.example.com")
        self.assertContains(response, "preview-myapp")
        self.assertNotContains(response, "Inherited from repository default branch")
        self.assertNotContains(response, "Inherited from app slug")

    def test_ws_editor_can_reset_deployment_with_draft_blueprint(self) -> None:
        self._set_open_blueprint_status(status=DeploymentBlueprint.Status.DRAFT)
        conversation = Conversation.objects.create(
            user=self.ws_editor,
            organization=self.org,
            context_workspace=self.workspace,
            context_repository=self.repo,
            context_app=self.app,
            context_deployment_blueprint=self.blueprint,
            mode=Conversation.Mode.APP_DEPLOYMENT,
            status=Conversation.Status.ACTIVE,
        )
        self.client.force_login(self.ws_editor)
        response = self.client.post("/deploy/myapp/reset/", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["HX-Replace-Url"], "/deploy/myapp/")
        self.blueprint.refresh_from_db()
        self.assertEqual(self.blueprint.status, DeploymentBlueprint.Status.DISCARDED)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.Status.ABANDONED)
        new_conversation = response.context["conversation"]
        self.assertNotEqual(new_conversation.id, conversation.id)
        self.assertEqual(new_conversation.context_app_id, self.app.id)

    def test_ws_viewer_gets_403_on_deployment_editor(self) -> None:
        self.client.force_login(self.ws_viewer)
        response = self.client.get("/deploy/myapp/", **HTMX)
        self.assertEqual(response.status_code, 403)

    def test_ws_viewer_gets_403_on_deployment_reset(self) -> None:
        self._set_open_blueprint_status(status=DeploymentBlueprint.Status.DRAFT)
        self.client.force_login(self.ws_viewer)
        response = self.client.post("/deploy/myapp/reset/", **HTMX)
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
