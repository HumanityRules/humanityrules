"""Collapse DeploymentBlueprint into App.

An app deploys to exactly one environment, so the (app, environment) join model
carries no information: its runtime fields move onto App, Deployment reaches the
environment through its app, and the redundant environment FKs on the permission
and activity models are dropped.

The data copy fails loudly instead of guessing: every App must have exactly one
source blueprint, and every dependent row must agree with the app's environment.
Clean up offending rows manually before migrating.
"""

import django.db.models.deletion
from django.db import migrations, models


def _copy_blueprint_fields_to_apps(apps, schema_editor):
    App = apps.get_model("humanityrules_app", "App")
    DeploymentBlueprint = apps.get_model("humanityrules_app", "DeploymentBlueprint")
    Deployment = apps.get_model("humanityrules_app", "Deployment")
    AppPermissions = apps.get_model("humanityrules_app", "AppPermissions")
    AppPermissionRequest = apps.get_model("humanityrules_app", "AppPermissionRequest")
    AppEnvironmentActivity = apps.get_model("humanityrules_app", "AppEnvironmentActivity")
    WebappPublicGrant = apps.get_model("humanityrules_app", "WebappPublicGrant")

    apps_without_blueprint = []
    apps_with_multiple_blueprints = []
    subdomain_holders = []
    for app in App.objects.all().iterator():
        blueprints = list(DeploymentBlueprint.objects.filter(app=app))
        if not blueprints:
            apps_without_blueprint.append(f"{app.organization_id}/{app.slug} ({app.id})")
            continue
        if len(blueprints) > 1:
            apps_with_multiple_blueprints.append(f"{app.organization_id}/{app.slug} ({len(blueprints)} blueprints)")
            continue
        blueprint = blueprints[0]
        if blueprint.subdomain and blueprint.subdomain != app.slug:
            subdomain_holders.append(f"blueprint {blueprint.id} (app {app.slug}, subdomain {blueprint.subdomain!r})")
        app.environment_id = blueprint.environment_id
        app.cpu = blueprint.cpu
        app.memory = blueprint.memory
        app.compute_mode = blueprint.compute_mode
        app.containers = blueprint.containers
        app.save(update_fields=["environment_id", "cpu", "memory", "compute_mode", "containers"])

    if apps_without_blueprint:
        raise RuntimeError(
            "Apps without a DeploymentBlueprint cannot be assigned an environment; "
            f"delete them (or create the missing blueprint) before migrating: {', '.join(apps_without_blueprint)}"
        )
    if apps_with_multiple_blueprints:
        raise RuntimeError(
            "Apps with several blueprints have no single authoritative runtime config; "
            f"delete the stale rows before migrating: {', '.join(apps_with_multiple_blueprints)}"
        )

    for row in Deployment.objects.exclude(subdomain="").exclude(subdomain=models.F("app__slug")).iterator():
        subdomain_holders.append(f"deployment {row.id} (app {row.app.slug}, subdomain {row.subdomain!r})")
    if subdomain_holders:
        raise RuntimeError(
            "Rows carrying an explicit subdomain different from the app slug would lose their "
            f"hostname when the field drops; reconcile them before migrating: {', '.join(subdomain_holders)}"
        )

    mismatches = []
    for model, label in (
        (Deployment, "Deployment"),
        (AppPermissions, "AppPermissions"),
        (AppPermissionRequest, "AppPermissionRequest"),
        (AppEnvironmentActivity, "AppEnvironmentActivity"),
        (WebappPublicGrant, "WebappPublicGrant"),
    ):
        for row in model.objects.exclude(environment_id=models.F("app__environment_id")).iterator():
            mismatches.append(f"{label} {row.id} (env {row.environment_id} != app env {row.app.environment_id})")

    if mismatches:
        raise RuntimeError(
            "Rows whose environment disagrees with their app's environment would be silently "
            f"reattached; resolve them before migrating: {', '.join(mismatches)}"
        )

    duplicate_holders = []
    for model, label in ((AppPermissions, "AppPermissions"), (AppEnvironmentActivity, "AppEnvironmentActivity")):
        duplicated_app_ids = (
            model.objects.values("app_id")
            .annotate(row_count=models.Count("id"))
            .filter(row_count__gt=1)
            .values_list("app_id", flat=True)
        )
        duplicate_holders.extend(f"{label} app={app_id}" for app_id in duplicated_app_ids)

    if duplicate_holders:
        raise RuntimeError(
            f"Multiple rows per app would violate the new one-to-one constraints; dedupe before migrating: {', '.join(duplicate_holders)}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0024_drop_datastore"),
    ]

    operations = [
        # New App fields, nullable while the data copy runs.
        migrations.AddField(
            model_name="app",
            name="environment",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="apps",
                to="humanityrules_app.environment",
                help_text="The environment this app deploys to. Set at creation, immutable; the app is deleted when its environment is torn down.",
            ),
        ),
        migrations.AddField(
            model_name="app",
            name="cpu",
            field=models.IntegerField(null=True, help_text="ECS task CPU units (256, 512, 1024, etc.)"),
        ),
        migrations.AddField(
            model_name="app",
            name="memory",
            field=models.IntegerField(null=True, help_text="ECS task memory in MiB"),
        ),
        migrations.AddField(
            model_name="app",
            name="compute_mode",
            field=models.CharField(
                choices=[("fargate", "Fargate"), ("ec2", "EC2 Capacity")],
                default="fargate",
                help_text="ECS compute backend for this app.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="app",
            name="containers",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(_copy_blueprint_fields_to_apps, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="app",
            name="environment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="apps",
                to="humanityrules_app.environment",
                help_text="The environment this app deploys to. Set at creation, immutable; the app is deleted when its environment is torn down.",
            ),
        ),
        migrations.AlterField(
            model_name="app",
            name="cpu",
            field=models.IntegerField(help_text="ECS task CPU units (256, 512, 1024, etc.)"),
        ),
        migrations.AlterField(
            model_name="app",
            name="memory",
            field=models.IntegerField(help_text="ECS task memory in MiB"),
        ),
        # Dropped fields: branch collapses into Repository.default_branch, subdomain
        # into App.slug, and blueprint/environment are reached through the app.
        migrations.RemoveField(model_name="app", name="branch"),
        migrations.RemoveField(model_name="deployment", name="blueprint"),
        migrations.RemoveField(model_name="deployment", name="environment"),
        migrations.RemoveField(model_name="deployment", name="subdomain"),
        # Redundant environment FKs on the permission/activity models.
        migrations.AlterUniqueTogether(name="apppermissions", unique_together=set()),
        migrations.RemoveField(model_name="apppermissions", name="environment"),
        migrations.AlterField(
            model_name="apppermissions",
            name="app",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="app_permissions",
                to="humanityrules_app.app",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="apppermissionrequest",
            name="unique_applying_permission_request_per_target",
        ),
        migrations.RemoveField(model_name="apppermissionrequest", name="environment"),
        migrations.AddConstraint(
            model_name="apppermissionrequest",
            constraint=models.UniqueConstraint(
                fields=["app"],
                condition=models.Q(status="applying"),
                name="unique_applying_permission_request_per_app",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="appenvironmentactivity",
            name="unique_app_environment_activity",
        ),
        migrations.RemoveField(model_name="appenvironmentactivity", name="environment"),
        migrations.AlterField(
            model_name="appenvironmentactivity",
            name="app",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="environment_activity",
                to="humanityrules_app.app",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="webapppublicgrant",
            name="unique_unrevoked_webapp_grant",
        ),
        migrations.RemoveIndex(
            model_name="webapppublicgrant",
            name="humanityrul_app_id_8f1b46_idx",
        ),
        migrations.RemoveField(model_name="webapppublicgrant", name="environment"),
        migrations.AddConstraint(
            model_name="webapppublicgrant",
            constraint=models.UniqueConstraint(
                fields=["app", "slug"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_unrevoked_webapp_grant",
            ),
        ),
        migrations.AddIndex(
            model_name="webapppublicgrant",
            index=models.Index(fields=["app", "slug"], name="webapp_grant_app_slug_idx"),
        ),
        migrations.DeleteModel(name="DeploymentBlueprint"),
    ]
