"""Tests for migrating persisted Hermes model configuration to one preset."""

import importlib

import django.apps
from django.test import TestCase

from humanityrules_app import models


llm_preset_migration = importlib.import_module(
    "humanityrules_app.migrations.0017_hermes_llm_preset_environment"
)

LEGACY_LLM_ENVIRONMENT = [
    {"name": "HUMR_LLM_PROVIDER", "value": "openai-codex"},
    {"name": "HUMR_LLM_MODEL", "value": "gpt-5.5"},
    {"name": "HUMR_LLM_BASE_URL", "value": ""},
    {"name": "HUMR_AUX_PROVIDER", "value": "openai-codex"},
    {"name": "HUMR_AUX_MODEL", "value": "gpt-5.5"},
    {"name": "HUMR_AUX_BASE_URL", "value": ""},
]


class HermesLlmPresetMigrationTests(TestCase):

    def setUp(self) -> None:
        organization = models.Organization.objects.create(
            name="Bedrock Org",
            slug="bedrock-org",
            llm_preset=models.Organization.LlmPreset.BEDROCK,
        )
        aws_account = models.AWSAccount.objects.create(
            organization=organization,
            name="Bedrock AWS",
        )
        environment = models.Environment.objects.create(
            aws_account=aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
        )
        repository = models.Repository.objects.create(
            organization=organization,
            provider=models.Repository.Provider.GITHUB,
            name="hermes-agent",
            full_name="HumanityRules/hermes-agent",
            default_branch="main",
            clone_url="https://github.com/HumanityRules/hermes-agent.git",
        )
        workspace = models.Workspace.objects.create(
            organization=organization,
            name="Assistants",
            slug="assistants",
        )
        self.template = models.AppTemplate.objects.create(
            name="Hermes Personal Assistant",
            slug="hermes-personal",
            description="Hermes",
            icon="robot",
            category="ai-assistant",
            cpu=1024,
            memory=4096,
            containers=[
                {
                    "name": "hermes",
                    "configurable_variables": [
                        *[
                            {
                                "name": entry["name"],
                                "category": "config",
                                "value": entry["value"],
                            }
                            for entry in LEGACY_LLM_ENVIRONMENT
                        ],
                        {
                            "name": "AWS_DEFAULT_REGION",
                            "category": "config",
                            "value": "us-east-1",
                        },
                    ],
                },
                {"name": "policy-proxy", "configurable_variables": []},
            ],
            is_active=True,
        )
        app = models.App.objects.create(
            organization=organization,
            workspace=workspace,
            repository=repository,
            source_template=self.template,
            name="Wolfie",
            slug="wolfie",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8787,
            health_check_path="/health",
        )
        self.blueprint = models.DeploymentBlueprint.objects.create(
            app=app,
            environment=environment,
            cpu=1024,
            memory=4096,
            containers=[
                {
                    "name": "hermes",
                    "environment_variables": [
                        *LEGACY_LLM_ENVIRONMENT,
                        {"name": "AWS_DEFAULT_REGION", "value": "us-east-1"},
                    ],
                    "app_secrets": {},
                },
                {
                    "name": "policy-proxy",
                    "environment_variables": [
                        {"name": "HUMR_POLICY_PROXY_UPSTREAM", "value": "hermes:8787"},
                    ],
                    "app_secrets": {},
                },
            ],
        )

    def test_migration_replaces_legacy_template_and_blueprint_environment(self) -> None:
        llm_preset_migration.patch_hermes_llm_preset_environment(
            apps=django.apps.apps,
            schema_editor=None,
        )

        self.template.refresh_from_db()
        self.blueprint.refresh_from_db()
        template_hermes = next(
            container for container in self.template.containers if container["name"] == "hermes"
        )
        blueprint_hermes = next(
            container for container in self.blueprint.containers if container["name"] == "hermes"
        )

        template_names = [
            variable["name"] for variable in template_hermes["configurable_variables"]
        ]
        blueprint_environment = {
            variable["name"]: variable["value"]
            for variable in blueprint_hermes["environment_variables"]
        }
        self.assertEqual(template_names, ["HUMR_LLM_PRESET", "AWS_DEFAULT_REGION"])
        self.assertEqual(
            template_hermes["configurable_variables"][0],
            llm_preset_migration.TEMPLATE_PRESET_VARIABLE,
        )
        self.assertEqual(
            blueprint_environment,
            {"HUMR_LLM_PRESET": "bedrock", "AWS_DEFAULT_REGION": "us-east-1"},
        )

    def test_migration_is_idempotent_and_leaves_sibling_containers_unchanged(self) -> None:
        for _iteration in range(2):
            llm_preset_migration.patch_hermes_llm_preset_environment(
                apps=django.apps.apps,
                schema_editor=None,
            )

        self.blueprint.refresh_from_db()
        proxy = next(
            container for container in self.blueprint.containers if container["name"] == "policy-proxy"
        )
        hermes = next(
            container for container in self.blueprint.containers if container["name"] == "hermes"
        )

        self.assertEqual(
            proxy["environment_variables"],
            [{"name": "HUMR_POLICY_PROXY_UPSTREAM", "value": "hermes:8787"}],
        )
        self.assertEqual(
            hermes["environment_variables"],
            [
                {"name": "HUMR_LLM_PRESET", "value": "bedrock"},
                {"name": "AWS_DEFAULT_REGION", "value": "us-east-1"},
            ],
        )
