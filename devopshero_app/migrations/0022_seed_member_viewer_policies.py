from typing import Any

from django.db import migrations


def add_member_viewer_policies(apps: Any, schema_editor: Any) -> None:
    """Add member and viewer seed policies to all existing organizations."""
    Organization = apps.get_model("devopshero_app", "Organization")
    Policy = apps.get_model("devopshero_app", "Policy")

    new_seed_policies = [
        {
            "name": "Org members: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["workspace:view", "workspace:edit"],
        },
        {
            "name": "Org members: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["environment:view", "environment:deploy"],
        },
        {
            "name": "Org members: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["app:use"],
        },
        {
            "name": "Org viewers: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["workspace:view"],
        },
        {
            "name": "Org viewers: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["environment:view"],
        },
        {
            "name": "Org viewers: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["app:use"],
        },
    ]

    for org in Organization.objects.all():
        for seed in new_seed_policies:
            Policy.objects.get_or_create(
                organization=org,
                name=seed["name"],
                defaults={
                    "resource_type": seed["resource_type"],
                    "identity_conditions": seed["identity_conditions"],
                    "resource_conditions": [{"key": "*", "value": "*"}],
                    "actions": seed["actions"],
                    "is_system": True,
                },
            )


def reverse_member_viewer_policies(apps: Any, schema_editor: Any) -> None:
    """Remove member and viewer seed policies."""
    Policy = apps.get_model("devopshero_app", "Policy")
    member_viewer_names = [
        "Org members: workspace access",
        "Org members: environment access",
        "Org members: app usage",
        "Org viewers: workspace access",
        "Org viewers: environment access",
        "Org viewers: app usage",
    ]
    Policy.objects.filter(name__in=member_viewer_names, is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('devopshero_app', '0021_bootstrap_abac_existing_orgs'),
    ]

    operations = [
        migrations.RunPython(add_member_viewer_policies, reverse_member_viewer_policies),
    ]
