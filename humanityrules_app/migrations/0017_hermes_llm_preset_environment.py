from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


HERMES_TEMPLATE_SLUG = "hermes-personal"
HERMES_CONTAINER_NAME = "hermes"
LLM_PRESET_ENV_VAR = "HUMR_LLM_PRESET"
SUPPORTED_LLM_PRESETS = frozenset({"bedrock", "codex"})
LEGACY_LLM_ENV_VARS = frozenset({
    "HUMR_LLM_PROVIDER",
    "HUMR_LLM_MODEL",
    "HUMR_LLM_BASE_URL",
    "HUMR_AUX_PROVIDER",
    "HUMR_AUX_MODEL",
    "HUMR_AUX_BASE_URL",
})
TEMPLATE_PRESET_VARIABLE = {
    "name": LLM_PRESET_ENV_VAR,
    "group": "LLM",
    "category": "config",
    "description": "HumR-managed LLM preset: codex or bedrock",
    "required": True,
    "auto_generate": False,
    "default_value": "codex",
    "value": "codex",
    "user_editable": True,
}


def _replace_model_variables(entries: list[dict], replacement: dict) -> list[dict]:
    """Replace legacy model variables and any existing preset with one entry."""
    removable_names = LEGACY_LLM_ENV_VARS | {LLM_PRESET_ENV_VAR}
    result: list[dict] = []
    insertion_index: int | None = None
    for entry in entries or []:
        if entry.get("name") in removable_names:
            if insertion_index is None:
                insertion_index = len(result)
            continue
        result.append(entry)
    result.insert(insertion_index if insertion_index is not None else 0, dict(replacement))
    return result


def _patch_template_containers(containers: list[dict]) -> list[dict]:
    """Replace model variables in the Hermes AppTemplate container."""
    result: list[dict] = []
    for container in containers or []:
        updated = dict(container)
        if updated.get("name") == HERMES_CONTAINER_NAME:
            updated["configurable_variables"] = _replace_model_variables(
                entries=list(updated.get("configurable_variables") or []),
                replacement=TEMPLATE_PRESET_VARIABLE,
            )
        result.append(updated)
    return result


def _patch_blueprint_containers(containers: list[dict], preset: str) -> list[dict]:
    """Replace materialized model variables in one Hermes blueprint."""
    result: list[dict] = []
    for container in containers or []:
        updated = dict(container)
        if updated.get("name") == HERMES_CONTAINER_NAME:
            updated["environment_variables"] = _replace_model_variables(
                entries=list(updated.get("environment_variables") or []),
                replacement={"name": LLM_PRESET_ENV_VAR, "value": preset},
            )
        result.append(updated)
    return result


def patch_hermes_llm_preset_environment(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Migrate Hermes templates and blueprints to the stable preset variable."""
    del schema_editor
    app_template = apps.get_model("humanityrules_app", "AppTemplate")
    deployment_blueprint = apps.get_model("humanityrules_app", "DeploymentBlueprint")

    for template in app_template.objects.filter(slug=HERMES_TEMPLATE_SLUG).iterator():
        template.containers = _patch_template_containers(containers=list(template.containers or []))
        template.save(update_fields=["containers"])

    blueprints = (
        deployment_blueprint.objects
        .filter(app__source_template__slug=HERMES_TEMPLATE_SLUG)
        .select_related("app__organization")
    )
    for blueprint in blueprints.iterator():
        preset = blueprint.app.organization.llm_preset or "codex"
        if preset not in SUPPORTED_LLM_PRESETS:
            raise RuntimeError(
                f"Cannot migrate blueprint {blueprint.pk}: unsupported LLM preset {preset!r}"
            )
        blueprint.containers = _patch_blueprint_containers(
            containers=list(blueprint.containers or []),
            preset=preset,
        )
        blueprint.save(update_fields=["containers"])


class Migration(migrations.Migration):
    dependencies = [
        ("humanityrules_app", "0016_serialize_cost_refreshes"),
    ]

    operations = [
        migrations.RunPython(
            code=patch_hermes_llm_preset_environment,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
