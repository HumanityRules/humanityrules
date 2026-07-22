"""Collapse the Deployment job table into App runtime fields plus an append-only event table.

App gains job_status/live_state/outputs/attempt fields; DeploymentRecord replaces
Deployment as insert-only audit; DeploymentLog is re-keyed to (app, attempt_id),
reusing old deployment ids as attempt ids so existing logs stay reachable.
AppRemovalJob folds into App.job_status. No event backfill.

Fails loudly if any deployment or removal is in flight — run with the fleet quiet.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

SETTLED_STATUSES = ("succeeded", "failed", "torn_down")


def backfill(apps, schema_editor):
    App = apps.get_model("humanityrules_app", "App")
    Deployment = apps.get_model("humanityrules_app", "Deployment")
    DeploymentLog = apps.get_model("humanityrules_app", "DeploymentLog")
    AppRemovalJob = apps.get_model("humanityrules_app", "AppRemovalJob")

    unsettled = list(Deployment.objects.exclude(status__in=SETTLED_STATUSES).values_list("id", "app__slug", "status"))
    if unsettled:
        raise RuntimeError(f"Refusing to migrate with unsettled deployments in flight: {unsettled}")
    active_removals = list(AppRemovalJob.objects.filter(status__in=("pending", "running")).values_list("id", "app_slug_snapshot"))
    if active_removals:
        raise RuntimeError(f"Refusing to migrate with active app removals: {active_removals}")
    pending_removal_apps = list(App.objects.filter(status="pending_removal").values_list("slug", flat=True))
    if pending_removal_apps:
        raise RuntimeError(f"Refusing to migrate with apps pending removal: {pending_removal_apps}")

    for app in App.objects.all():
        deployments = list(Deployment.objects.filter(app=app).order_by("-created_at"))
        if not deployments:
            continue
        latest = deployments[0]
        app.last_attempt_id = latest.id
        app.last_attempt_error = latest.status_message if latest.status == "failed" else ""
        # Same priority the old get_current_deployment used among settled rows:
        # the newest succeeded/torn_down row is authoritative for live state.
        authoritative = next((d for d in deployments if d.status in ("succeeded", "torn_down")), None)
        if authoritative is not None and authoritative.status == "succeeded":
            app.live_state = "deployed"
            app.service_url = authoritative.service_url
            app.alb_dns = authoritative.alb_dns
            app.last_deployed_at = authoritative.completed_at
        elif authoritative is not None:
            app.live_state = "torn_down"
        app.may_have_infra = latest.status != "torn_down"
        app.save()

    for deployment in Deployment.objects.all().only("id", "app_id"):
        DeploymentLog.objects.filter(deployment_id=deployment.id).update(
            app_id=deployment.app_id,
            attempt_id=deployment.id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0028_remove_app_type"),
    ]

    operations = [
        # --- New App runtime fields ---
        migrations.AddField(
            model_name="app",
            name="job_status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("deploy_pending", "Deploy Pending"),
                    ("deploying", "Deploying"),
                    ("teardown_pending", "Teardown Pending"),
                    ("tearing_down", "Tearing Down"),
                    ("removal_pending", "Removal Pending"),
                    ("removing", "Removing"),
                ],
                default="idle",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="app",
            name="live_state",
            field=models.CharField(
                choices=[
                    ("not_deployed", "Not Deployed"),
                    ("deployed", "Deployed"),
                    ("torn_down", "Torn Down"),
                ],
                default="not_deployed",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="app",
            name="may_have_infra",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="app",
            name="service_url",
            field=models.URLField(blank=True, help_text="URL where the deployed service is accessible", max_length=2048),
        ),
        migrations.AddField(
            model_name="app",
            name="alb_dns",
            field=models.CharField(blank=True, help_text="ALB DNS name", max_length=255),
        ),
        migrations.AddField(
            model_name="app",
            name="last_deployed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="app",
            name="last_attempt_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="app",
            name="last_attempt_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="app",
            name="removal_delete_all_data",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="app",
            name="removal_teardown_first",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="app",
            name="claimed_by_run",
            field=models.ForeignKey(
                blank=True,
                help_text="Worker run that claimed the in-flight job; liveness input for stale-job detection",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="humanityrules_app.jobworkerrun",
            ),
        ),
        # --- Append-only event table ---
        migrations.CreateModel(
            name="DeploymentRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ("attempt_id", models.UUIDField(db_index=True)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("deploy_started", "Deploy Started"),
                            ("deploy_succeeded", "Deploy Succeeded"),
                            ("deploy_failed", "Deploy Failed"),
                            ("teardown_started", "Teardown Started"),
                            ("teardown_succeeded", "Teardown Succeeded"),
                            ("teardown_failed", "Teardown Failed"),
                            ("removal_started", "Removal Started"),
                            ("removal_failed", "Removal Failed"),
                        ],
                        max_length=30,
                    ),
                ),
                ("git_ref", models.CharField(blank=True, help_text="Branch deployed by this attempt (deploy events only)", max_length=255)),
                ("error", models.TextField(blank=True, help_text="Failure message (failure events only)")),
                ("details", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deployment_records",
                        to="humanityrules_app.app",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_deployment_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        # --- Re-key DeploymentLog to (app, attempt_id) ---
        migrations.AddField(
            model_name="deploymentlog",
            name="app",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deployment_logs",
                to="humanityrules_app.app",
            ),
        ),
        migrations.AddField(
            model_name="deploymentlog",
            name="attempt_id",
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="deploymentlog",
            name="app",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deployment_logs",
                to="humanityrules_app.app",
            ),
        ),
        migrations.AlterField(
            model_name="deploymentlog",
            name="attempt_id",
            field=models.UUIDField(db_index=True),
        ),
        migrations.RemoveField(
            model_name="deploymentlog",
            name="deployment",
        ),
        # --- Drop the old job tables and App.status ---
        migrations.RemoveField(
            model_name="app",
            name="status",
        ),
        migrations.DeleteModel(name="Deployment"),
        migrations.DeleteModel(name="AppRemovalJob"),
    ]
