"""Tests for the historical migration that collapsed persisted Hermes model configuration to one preset.

Migration 0017 ran over DeploymentBlueprint rows, a model deleted by migration 0025, so its
schema can no longer be materialized in the test database. The container-patching logic it
applied is pure, so these tests drive the migration module's patch functions directly on the
same template/blueprint container fixtures the original DB-backed tests used.
"""

import importlib

from django.test import SimpleTestCase

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


def _template_containers() -> list[dict]:
    """Build the Hermes AppTemplate containers shape the migration patched."""
    return [
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
    ]


def _blueprint_containers() -> list[dict]:
    """Build the materialized blueprint containers shape the migration patched."""
    return [
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
    ]


class HermesLlmPresetMigrationTests(SimpleTestCase):

    def test_migration_replaces_legacy_template_and_blueprint_environment(self) -> None:
        patched_template = llm_preset_migration._patch_template_containers(containers=_template_containers())
        patched_blueprint = llm_preset_migration._patch_blueprint_containers(
            containers=_blueprint_containers(),
            preset="bedrock",
        )

        template_hermes = next(container for container in patched_template if container["name"] == "hermes")
        blueprint_hermes = next(container for container in patched_blueprint if container["name"] == "hermes")

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
        containers = _blueprint_containers()
        for _iteration in range(2):
            containers = llm_preset_migration._patch_blueprint_containers(
                containers=containers,
                preset="bedrock",
            )

        proxy = next(container for container in containers if container["name"] == "policy-proxy")
        hermes = next(container for container in containers if container["name"] == "hermes")

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
