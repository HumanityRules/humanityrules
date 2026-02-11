from django.db import migrations, models


def migrate_running_to_deployed(apps, schema_editor):
    Deployment = apps.get_model("devopshero_app", "Deployment")
    Deployment.objects.filter(status="running").update(status="deployed")


def migrate_deployed_to_running(apps, schema_editor):
    Deployment = apps.get_model("devopshero_app", "Deployment")
    Deployment.objects.filter(status="deployed").update(status="running")


class Migration(migrations.Migration):

    dependencies = [
        ("devopshero_app", "0011_alter_deployment_status"),
    ]

    operations = [
        migrations.RunPython(migrate_running_to_deployed, migrate_deployed_to_running),
        migrations.AlterField(
            model_name="deployment",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("building", "Building Image"),
                    ("pushing", "Pushing to ECR"),
                    ("deploying", "Deploying Infrastructure"),
                    ("starting", "Starting Service"),
                    ("deployed", "Deployed"),
                    ("failed", "Failed"),
                    ("rolled_back", "Rolled Back"),
                    ("superseded", "Superseded"),
                    ("torn_down", "Torn Down"),
                    ("teardown_pending", "Teardown Pending"),
                    ("tearing_down", "Tearing Down"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
