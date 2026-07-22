"""Apps become template-sourced only: drop the git/build fields and the vestigial template Repository rows.

Fails loudly if any App lacks a source_template — such rows cannot deploy
(the config builder hard-fails without a template) and must be resolved by
hand before this migration runs.
"""

import django.db.models.deletion
from django.db import migrations, models


def _require_all_apps_template_sourced(apps, schema_editor):
    App = apps.get_model("humanityrules_app", "App")
    orphans = list(App.objects.filter(source_template__isnull=True).values_list("slug", flat=True))
    if orphans:
        raise RuntimeError(
            f"Apps without a source_template cannot survive the template-sourced migration: {orphans}. "
            "Delete them or attach a template, then re-run."
        )


def _delete_template_repository_rows(apps, schema_editor):
    Repository = apps.get_model("humanityrules_app", "Repository")
    Repository.objects.filter(provider="local", full_name__startswith="template/").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0030_alter_deploymentrecord_options"),
    ]

    operations = [
        migrations.RunPython(_require_all_apps_template_sourced, migrations.RunPython.noop),
        migrations.RemoveField(model_name="app", name="repository"),
        migrations.RemoveField(model_name="app", name="build_strategy"),
        migrations.RemoveField(model_name="app", name="repo_subpath"),
        migrations.RemoveField(model_name="app", name="dockerfile_path"),
        migrations.AlterField(
            model_name="app",
            name="source_template",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deployed_apps",
                to="humanityrules_app.apptemplate",
            ),
        ),
        migrations.RemoveField(model_name="deploymentrecord", name="git_ref"),
        migrations.RunPython(_delete_template_repository_rows, migrations.RunPython.noop),
    ]
