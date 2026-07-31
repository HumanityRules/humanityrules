"""Put entitlements on a plan: add Organization.plan + plan_overrides, drop platform_capabilities.

Capabilities become booleans inside the plan config, so the granular escape
hatch is an override rather than a second list of slugs the app has to reconcile
with the plan. Every org holding the bedrock-runtime slug keeps it as
plan_overrides = {"bedrock_enabled": true}; the deploy path derives the slug
list back out of the booleans.

Every org lands on trial. The 500-credit trial grant is a ledger write, not a
column, so it is not written here — see humr_billing_backfill_trial_grants.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


BEDROCK_RUNTIME_CAPABILITY = "bedrock-runtime"


def move_bedrock_capability_into_plan_overrides(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Carry each org's bedrock-runtime slug over as the plan override that now grants it."""
    del schema_editor
    organization = apps.get_model("humanityrules_app", "Organization")

    for org in organization.objects.all():
        if BEDROCK_RUNTIME_CAPABILITY not in (org.platform_capabilities or []):
            continue
        overrides = dict(org.plan_overrides or {})
        overrides["bedrock_enabled"] = True
        org.plan_overrides = overrides
        org.save(update_fields=["plan_overrides"])


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0034_billingbalance_billingledgerentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="plan",
            field=models.CharField(
                choices=[
                    ("trial", "Trial"),
                    ("operator", "Operator"),
                    ("team", "Team"),
                    ("enterprise", "Enterprise"),
                ],
                default="trial",
                help_text="Entitlement tier; the numbers behind it live in services/billing/plans.py.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="organization",
            name="plan_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Entitlement fields this org overrides, e.g. {"bedrock_enabled": true}. Never price.',
            ),
        ),
        migrations.RunPython(
            code=move_bedrock_capability_into_plan_overrides,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="organization",
            name="platform_capabilities",
        ),
    ]
