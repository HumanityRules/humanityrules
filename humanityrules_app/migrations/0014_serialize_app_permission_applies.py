from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Count
from django.utils import timezone


MIGRATION_FAILURE_MESSAGE = "Superseded while enabling serialized permission applies"


def fail_duplicate_applying_requests(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Keep the newest applying request per target and conclude older duplicates."""
    app_permission_request = apps.get_model("humanityrules_app", "AppPermissionRequest")
    duplicate_targets = (
        app_permission_request.objects
        .filter(status="applying")
        .values("app_id", "environment_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for target in duplicate_targets.iterator():
        keep_id = (
            app_permission_request.objects
            .filter(
                app_id=target["app_id"],
                environment_id=target["environment_id"],
                status="applying",
            )
            .order_by("-updated_at", "-created_at")
            .values_list("id", flat=True)
            .first()
        )
        app_permission_request.objects.filter(
            app_id=target["app_id"],
            environment_id=target["environment_id"],
            status="applying",
        ).exclude(id=keep_id).update(
            status="failed",
            status_message=MIGRATION_FAILURE_MESSAGE,
            updated_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("humanityrules_app", "0013_app_environment_activity"),
    ]

    operations = [
        migrations.RunPython(
            code=fail_duplicate_applying_requests,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="apppermissionrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="applying"),
                fields=("app", "environment"),
                name="unique_applying_permission_request_per_target",
            ),
        ),
    ]
