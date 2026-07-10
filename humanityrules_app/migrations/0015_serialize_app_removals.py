from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Count
from django.utils import timezone


ACTIVE_REMOVAL_STATUSES = ("pending", "running")
MIGRATION_FAILURE_MESSAGE = "Superseded while enabling serialized app removals"


def fail_duplicate_active_removals(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Keep one active removal per app and conclude duplicate rows."""
    app_removal_job = apps.get_model("humanityrules_app", "AppRemovalJob")
    duplicate_app_ids = (
        app_removal_job.objects
        .filter(status__in=ACTIVE_REMOVAL_STATUSES)
        .values("app_id_snapshot")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicate_app_ids.iterator():
        keep_id = (
            app_removal_job.objects
            .filter(
                app_id_snapshot=duplicate["app_id_snapshot"],
                status__in=ACTIVE_REMOVAL_STATUSES,
            )
            .annotate(
                status_priority=models.Case(
                    models.When(status="running", then=0),
                    default=1,
                    output_field=models.IntegerField(),
                ),
            )
            .order_by("status_priority", "updated_at", "created_at")
            .values_list("id", flat=True)
            .first()
        )
        app_removal_job.objects.filter(
            app_id_snapshot=duplicate["app_id_snapshot"],
            status__in=ACTIVE_REMOVAL_STATUSES,
        ).exclude(id=keep_id).update(
            status="failed",
            status_message=MIGRATION_FAILURE_MESSAGE,
            updated_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("humanityrules_app", "0014_serialize_app_permission_applies"),
    ]

    operations = [
        migrations.RunPython(
            code=fail_duplicate_active_removals,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="appremovaljob",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=("pending", "running")),
                fields=("app_id_snapshot",),
                name="unique_active_app_removal_per_app",
            ),
        ),
    ]
