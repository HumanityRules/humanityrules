from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Count
from django.utils import timezone


ACTIVE_REFRESH_STATUSES = ("pending", "running")
MIGRATION_FAILURE_MESSAGE = "Superseded while enabling serialized cost refreshes"


def fail_duplicate_active_refreshes(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Keep one active cost refresh per app and conclude duplicate rows."""
    cost_refresh_job = apps.get_model("humanityrules_app", "CostRefreshJob")
    duplicate_app_ids = (
        cost_refresh_job.objects
        .filter(status__in=ACTIVE_REFRESH_STATUSES)
        .values("app_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicate_app_ids.iterator():
        keep_id = (
            cost_refresh_job.objects
            .filter(
                app_id=duplicate["app_id"],
                status__in=ACTIVE_REFRESH_STATUSES,
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
        cost_refresh_job.objects.filter(
            app_id=duplicate["app_id"],
            status__in=ACTIVE_REFRESH_STATUSES,
        ).exclude(id=keep_id).update(
            status="failed",
            status_message=MIGRATION_FAILURE_MESSAGE,
            updated_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("humanityrules_app", "0015_serialize_app_removals"),
    ]

    operations = [
        migrations.RunPython(
            code=fail_duplicate_active_refreshes,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="costrefreshjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=("pending", "running")),
                fields=("app",),
                name="unique_active_cost_refresh_per_app",
            ),
        ),
    ]
