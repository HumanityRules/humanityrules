"""Rename the shared-credential ABAC keys to clearer names.

    shared-scope     -> sharing-scope
    shared-user      -> shared-with-user
    shared-workspace -> shared-with-workspace

Reconciles databases migrated before the rename: rewrites credential
``ResourceTag`` rows and any ``Policy`` condition that references an old key
(including ``$resource.shared-user`` cross-references). New orgs already get the
new names from ``abac_service`` (bootstrap + ``sync_shared_credential_tags``).
"""

from django.db import migrations

# old key -> new key
RENAME = {
    "shared-scope": "sharing-scope",
    "shared-user": "shared-with-user",
    "shared-workspace": "shared-with-workspace",
}
_REFERENCE_SIDES = ("identity", "resource", "app")


def _rewrite_conditions(conditions: list, rename: dict) -> bool:
    """Apply *rename* to a condition list in place; return True if anything changed."""
    changed = False
    for condition in conditions:
        key = condition.get("key")
        if key in rename:
            condition["key"] = rename[key]
            changed = True
        value = condition.get("value")
        if isinstance(value, str):
            for side in _REFERENCE_SIDES:
                for old, new in rename.items():
                    if value == f"${side}.{old}":
                        condition["value"] = f"${side}.{new}"
                        changed = True
    return changed


def _apply_rename(apps, rename: dict) -> None:
    ResourceTag = apps.get_model("humanityrules_app", "ResourceTag")
    for old, new in rename.items():
        ResourceTag.objects.filter(resource_type="credential", key=old).update(key=new)

    Policy = apps.get_model("humanityrules_app", "Policy")
    for policy in Policy.objects.all():
        changed = _rewrite_conditions(policy.identity_conditions, rename)
        changed = _rewrite_conditions(policy.resource_conditions, rename) or changed
        if changed:
            policy.save(update_fields=["identity_conditions", "resource_conditions"])


def rename_forward(apps, schema_editor):
    _apply_rename(apps, RENAME)


def rename_backward(apps, schema_editor):
    _apply_rename(apps, {new: old for old, new in RENAME.items()})


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0006_alter_integrationsharedcredential_config_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
