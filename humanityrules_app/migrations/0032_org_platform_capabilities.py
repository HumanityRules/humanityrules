"""Move platform_capabilities from AppTemplate to Organization.

The grant is an org entitlement, not a property of the template being deployed.
Seeds bedrock-runtime for the platform-owner org and for every org whose LLM
preset is Bedrock (their assistants default to a Bedrock model and would boot
without the IAM behind it); every other org starts with no capabilities.
"""

from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Q


BEDROCK_RUNTIME_CAPABILITY = "bedrock-runtime"
BEDROCK_PRESET = "bedrock"


def grant_bedrock_runtime_capability(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Grant bedrock-runtime to the platform-owner org and every Bedrock-preset org."""
    del schema_editor
    organization = apps.get_model("humanityrules_app", "Organization")

    entitled = Q(llm_preset=BEDROCK_PRESET)
    owner_slug = settings.HUMR_PLATFORM_OWNER_ORG_SLUG
    if owner_slug:
        entitled |= Q(slug=owner_slug)

    organization.objects.filter(entitled).update(platform_capabilities=[BEDROCK_RUNTIME_CAPABILITY])


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0031_template_sourced_apps"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="platform_capabilities",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(
            code=grant_bedrock_runtime_capability,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="apptemplate",
            name="platform_capabilities",
        ),
    ]
