"""
Make App.slug unique per organization instead of globally unique.

This migration:
1. Adds nullable organization FK to App
2. Populates it from workspace.organization (data migration)
3. Makes organization non-nullable and adds unique constraint
4. Removes the old global unique constraint on slug
"""

from django.db import migrations, models
import django.db.models.deletion


def populate_app_organization(apps, schema_editor):
    """Set organization from workspace.organization for all existing apps."""
    App = apps.get_model("devopshero_app", "App")
    for app in App.objects.select_related("workspace__organization").all():
        app.organization = app.workspace.organization
        app.save(update_fields=["organization"])


def reverse_populate(apps, schema_editor):
    """No-op reverse: we're just clearing the field which will be dropped."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("devopshero_app", "0016_alter_deploymentlog_level"),
    ]

    operations = [
        # Step 1: Add organization as nullable
        migrations.AddField(
            model_name="app",
            name="organization",
            field=models.ForeignKey(
                help_text="Denormalized from workspace for unique constraint on (organization, slug)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="apps",
                to="devopshero_app.organization",
            ),
        ),
        # Step 2: Populate organization from workspace
        migrations.RunPython(populate_app_organization, reverse_populate),
        # Step 3: Make organization non-nullable
        migrations.AlterField(
            model_name="app",
            name="organization",
            field=models.ForeignKey(
                help_text="Denormalized from workspace for unique constraint on (organization, slug)",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="apps",
                to="devopshero_app.organization",
            ),
        ),
        # Step 4: Remove global unique constraint on slug
        migrations.AlterField(
            model_name="app",
            name="slug",
            field=models.SlugField(max_length=255),
        ),
        # Step 5: Add unique constraint on (organization, slug)
        migrations.AddConstraint(
            model_name="app",
            constraint=models.UniqueConstraint(
                fields=["organization", "slug"],
                name="unique_app_slug_per_org",
            ),
        ),
    ]
