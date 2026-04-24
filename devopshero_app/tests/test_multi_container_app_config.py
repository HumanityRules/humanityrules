"""Unit tests for the multi-container AppTemplate → AppConfig projection."""

from pathlib import Path

from django.test import TestCase

from devopshero_app.models import (
    App,
    AppTemplate,
    AWSAccount,
    DeploymentBlueprint,
    Environment,
    Organization,
    Repository,
    Workspace,
)
from devopshero_app.services.jobs import app_config_builder


def _hermes_slack_template() -> AppTemplate:
    return AppTemplate.objects.create(
        name="Hermes Slack",
        slug="hermes-slack",
        description="Two-container Hermes + sidecar-mcp.",
        icon="💬",
        category="ai-assistant",
        cpu=2048,
        memory=4096,
        alb_target_container="hermes",
        containers=[
            {
                "name": "hermes",
                "image_source": "dockerfile",
                "source_repo_path": "hermes_agent",
                "dockerfile_path": "Dockerfile",
                "container_port": 8787,
                "health_check_path": "/health",
                "efs_mount": True,
                "runtime_variables": [
                    {"name": "DOH_LLM_MODEL", "category": "config", "value": "model-x"},
                    {"name": "ANTHROPIC_API_KEY", "category": "secret", "value": ""},
                ],
            },
            {
                "name": "sidecar-mcp",
                "image_source": "prebuilt",
                "ecr_repo": "sidecar-mcp",
                "version": "0.1.0",
                "container_port": 7777,
                "health_check_command": "curl -fsS http://127.0.0.1:7777/health",
                "efs_mount": False,
                "runtime_variables": [
                    {"name": "GITHUB_TOKEN", "category": "secret", "value": ""},
                ],
            },
        ],
        efs_config={"mount_path": "/home/hermeswebui/.hermes", "posix_uid": 1024, "posix_gid": 1024},
        default_tags=[],
        platform_capabilities=["bedrock-runtime"],
        is_active=True,
    )


def _scaffold_blueprint(template: AppTemplate, blueprint_containers: list) -> DeploymentBlueprint:
    org = Organization.objects.create(name="Org", slug="org")
    aws_account = AWSAccount.objects.create(organization=org, name="Acct")
    env = Environment.objects.create(
        aws_account=aws_account, name="staging", slug="staging",
        aws_region="us-east-1", status=Environment.Status.READY,
    )
    workspace = Workspace.objects.get(organization=org, slug="default")
    repo = Repository.objects.create(
        organization=org, full_name="template/hermes-slack", name="hermes-slack",
        clone_url="file:///tmp/x", default_branch="main",
    )
    app = App.objects.create(
        organization=org, workspace=workspace, repository=repo, source_template=template,
        name="my-hermes", slug="my-hermes",
        app_type=App.AppType.WEB, build_strategy=App.BuildStrategy.DOCKERFILE,
        branch="main", container_port=8787, health_check_path="/health",
    )
    return DeploymentBlueprint.objects.create(
        app=app, environment=env, branch="main", cpu=2048, memory=4096,
        containers=blueprint_containers,
    )


class MultiContainerBuildAppConfigTests(TestCase):

    def test_projects_two_containers_with_per_container_env_and_secrets(self) -> None:
        template = _hermes_slack_template()
        blueprint = _scaffold_blueprint(template, [
            {
                "name": "hermes",
                "environment_variables": [{"name": "DOH_LLM_MODEL", "value": "model-x"}],
                "app_secrets": {"ANTHROPIC_API_KEY": ""},
            },
            {
                "name": "sidecar-mcp",
                "environment_variables": [],
                "app_secrets": {"GITHUB_TOKEN": ""},
            },
        ])

        app_config = app_config_builder.build_app_config_from_blueprint(
            blueprint=blueprint, repo_path=Path("/tmp/clone"),
        )

        self.assertEqual([c.name for c in app_config.containers], ["hermes", "sidecar-mcp"])
        self.assertEqual(app_config.alb_target_container, "hermes")
        self.assertEqual(app_config.platform_capabilities, ["bedrock-runtime"])
        hermes = app_config.containers[0]
        mcp = app_config.containers[1]
        self.assertEqual(hermes.image_source, "dockerfile")
        self.assertEqual(hermes.ecr_repo_name, "doh/staging/my-hermes-hermes")
        self.assertTrue(hermes.efs_mount)
        self.assertEqual(hermes.environment_variables, [{"name": "DOH_LLM_MODEL", "value": "model-x"}])
        self.assertEqual(hermes.app_secrets, {"ANTHROPIC_API_KEY": ""})
        self.assertEqual(mcp.image_source, "prebuilt")
        self.assertEqual(mcp.prebuilt_ecr_repo, "sidecar-mcp")
        self.assertEqual(mcp.prebuilt_version, "0.1.0")
        self.assertFalse(mcp.efs_mount)
        self.assertEqual(mcp.app_secrets, {"GITHUB_TOKEN": ""})
        # Union shows every container's declared secrets, no collisions.
        self.assertEqual(set(app_config.app_secrets or {}), {"ANTHROPIC_API_KEY", "GITHUB_TOKEN"})

    def test_alb_target_lookup_returns_correct_container(self) -> None:
        template = _hermes_slack_template()
        blueprint = _scaffold_blueprint(template, [
            {"name": "hermes", "environment_variables": [], "app_secrets": {}},
            {"name": "sidecar-mcp", "environment_variables": [], "app_secrets": {}},
        ])
        app_config = app_config_builder.build_app_config_from_blueprint(
            blueprint=blueprint, repo_path=Path("/tmp/clone"),
        )
        self.assertEqual(app_config.alb_target().name, "hermes")

    def test_same_secret_value_across_containers_unions_without_collision(self) -> None:
        template = _hermes_slack_template()
        blueprint = _scaffold_blueprint(template, [
            {
                "name": "hermes",
                "environment_variables": [],
                "app_secrets": {"SHARED_TOKEN": ""},
            },
            {
                "name": "sidecar-mcp",
                "environment_variables": [],
                "app_secrets": {"SHARED_TOKEN": ""},
            },
        ])
        app_config = app_config_builder.build_app_config_from_blueprint(
            blueprint=blueprint, repo_path=Path("/tmp/clone"),
        )
        self.assertEqual(app_config.app_secrets, {"SHARED_TOKEN": ""})

    def test_conflicting_secret_values_across_containers_raise_collision(self) -> None:
        template = _hermes_slack_template()
        blueprint = _scaffold_blueprint(template, [
            {
                "name": "hermes",
                "environment_variables": [],
                "app_secrets": {"SHARED_TOKEN": "literal-A"},
            },
            {
                "name": "sidecar-mcp",
                "environment_variables": [],
                "app_secrets": {"SHARED_TOKEN": "literal-B"},
            },
        ])
        with self.assertRaises(app_config_builder.ContainerSecretCollision):
            app_config_builder.build_app_config_from_blueprint(
                blueprint=blueprint, repo_path=Path("/tmp/clone"),
            )
