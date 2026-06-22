"""Backfill the three shared-credential ABAC system policies into existing orgs.

New orgs get these from abac_service.bootstrap_organization; this migration
covers orgs created before the credential resource type existed. Idempotent via
get_or_create on (organization, name).
"""

from django.db import migrations

CREDENTIAL_POLICIES = [
    {
        "name": "Shared credentials: everyone access",
        "identity_conditions": [{"key": "authenticated", "value": "true"}],
        "resource_conditions": [{"key": "shared-scope", "value": "everyone"}],
        "actions": ["credential:use"],
    },
    {
        "name": "Shared credentials: targeted user access",
        "identity_conditions": [{"key": "username", "value": "$resource.shared-user"}],
        "resource_conditions": [{"key": "shared-scope", "value": "user"}],
        "actions": ["credential:use"],
    },
    {
        "name": "Shared credentials: workspace access",
        "identity_conditions": [{"key": "authenticated", "value": "true"}],
        "resource_conditions": [{"key": "shared-workspace", "value": "$app.workspace-name"}],
        "actions": ["credential:use"],
    },
]


def seed_credential_policies(apps, schema_editor):
    Organization = apps.get_model("humanityrules_app", "Organization")
    Policy = apps.get_model("humanityrules_app", "Policy")
    for organization in Organization.objects.all():
        for policy in CREDENTIAL_POLICIES:
            Policy.objects.get_or_create(
                organization=organization,
                name=policy["name"],
                defaults={
                    "resource_type": "credential",
                    "identity_conditions": policy["identity_conditions"],
                    "resource_conditions": policy["resource_conditions"],
                    "actions": policy["actions"],
                    "is_system": True,
                },
            )


def remove_credential_policies(apps, schema_editor):
    Policy = apps.get_model("humanityrules_app", "Policy")
    names = [policy["name"] for policy in CREDENTIAL_POLICIES]
    Policy.objects.filter(resource_type="credential", name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0003_integrationsharedcredential_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_credential_policies, remove_credential_policies),
    ]
