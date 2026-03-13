"""Regression tests for the 0030 deployment blueprint backfill."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TestMigration0030BlueprintFkNonNullable(TransactionTestCase):
    """Verify legacy deployments are backfilled before blueprint becomes required."""

    migrate_from = [("devopshero_app", "0029_drop_app_runtime_fields")]
    migrate_to = [("devopshero_app", "0032_alter_deployment_blueprint_non_nullable")]

    def setUp(self) -> None:
        super().setUp()
        self.latest_target = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.latest_target)
        super().tearDown()

    def _create_legacy_deployment(self, status: str, subdomain: str) -> str:
        Organization = self.old_apps.get_model("devopshero_app", "Organization")
        AWSAccount = self.old_apps.get_model("devopshero_app", "AWSAccount")
        Repository = self.old_apps.get_model("devopshero_app", "Repository")
        Workspace = self.old_apps.get_model("devopshero_app", "Workspace")
        App = self.old_apps.get_model("devopshero_app", "App")
        Environment = self.old_apps.get_model("devopshero_app", "Environment")
        Deployment = self.old_apps.get_model("devopshero_app", "Deployment")

        organization = Organization.objects.create(name=f"Legacy Org {status}", slug=f"legacy-org-{status}")
        aws_account = AWSAccount.objects.create(organization=organization, name=f"Legacy AWS {status}")
        repository = Repository.objects.create(
            organization=organization,
            provider="github",
            name=f"repo-{status}",
            full_name=f"org/repo-{status}",
            clone_url=f"https://github.com/org/repo-{status}.git",
            default_branch="main",
        )
        workspace = Workspace.objects.create(
            organization=organization,
            name=f"Engineering {status}",
            slug=f"engineering-{status}",
        )
        app = App.objects.create(
            organization=organization,
            workspace=workspace,
            repository=repository,
            name=f"Legacy App {status}",
            slug=f"legacy-app-{status}",
            app_type="web",
            build_strategy="dockerfile",
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        environment = Environment.objects.create(
            aws_account=aws_account,
            name=f"Staging {status}",
            slug=f"staging-{status}",
            aws_region="us-east-1",
            status="ready",
        )
        deployment = Deployment.objects.create(
            app=app,
            environment=environment,
            subdomain=subdomain,
            git_ref="main",
            image_tag=f"legacy-{status}",
            status=status,
            status_message="Legacy deployment",
        )
        return str(deployment.id)

    def _migrate_forwards(self) -> None:
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        self.new_apps = self.executor.loader.project_state(self.migrate_to).apps

    def test_migration_backfills_succeeded_legacy_deployment(self) -> None:
        deployment_id = self._create_legacy_deployment(status="succeeded", subdomain="legacy-succeeded")

        self._migrate_forwards()

        Deployment = self.new_apps.get_model("devopshero_app", "Deployment")
        DeploymentBlueprint = self.new_apps.get_model("devopshero_app", "DeploymentBlueprint")

        deployment = Deployment.objects.get(id=deployment_id)
        blueprint = DeploymentBlueprint.objects.get(id=deployment.blueprint_id)

        self.assertEqual(deployment.status, "succeeded")
        self.assertFalse(Deployment._meta.get_field("blueprint").null)
        self.assertEqual(blueprint.status, "active")
        self.assertEqual(blueprint.branch, "main")
        self.assertEqual(blueprint.cpu, 256)
        self.assertEqual(blueprint.memory, 512)
        self.assertEqual(blueprint.environment_variables, [])
        self.assertIsNone(blueprint.app_secrets)
        self.assertEqual(blueprint.subdomain, "legacy-succeeded")
        self.assertEqual(blueprint.app_id, deployment.app_id)
        self.assertEqual(blueprint.environment_id, deployment.environment_id)
        self.assertEqual(
            blueprint.status_message,
            "Backfilled from legacy deployment. Runtime settings were defaulted to 256 CPU units and 512 MiB memory.",
        )

    def test_migration_marks_in_progress_legacy_deployment_failed(self) -> None:
        deployment_id = self._create_legacy_deployment(status="pending", subdomain="legacy-pending")

        self._migrate_forwards()

        Deployment = self.new_apps.get_model("devopshero_app", "Deployment")
        DeploymentBlueprint = self.new_apps.get_model("devopshero_app", "DeploymentBlueprint")

        deployment = Deployment.objects.get(id=deployment_id)
        blueprint = DeploymentBlueprint.objects.get(id=deployment.blueprint_id)

        self.assertEqual(deployment.status, "failed")
        self.assertEqual(
            deployment.status_message,
            "Marked failed during deployment blueprint backfill. Re-run the deployment or teardown from the UI.",
        )
        self.assertIsNotNone(deployment.completed_at)
        self.assertEqual(blueprint.status, "discarded")
