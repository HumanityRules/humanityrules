"""Drop the standalone-teardown job states from App.

"Tear Down" is no longer an operation a user can run: removal is the single
destructive action and tears infra down inline. With no writer left, the two
`job_status` phases (`teardown_pending`, `tearing_down`) and the `live_state`
result they produced (`torn_down`) come out of the enums. Choices-only — no
rows carry any of the three values, so there is nothing to migrate.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0042_drop_app_removal_flags"),
    ]

    operations = [
        migrations.AlterField(
            model_name="app",
            name="job_status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("deploy_pending", "Deploy Pending"),
                    ("deploying", "Deploying"),
                    ("removal_pending", "Removal Pending"),
                    ("removing", "Removing"),
                ],
                default="idle",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="app",
            name="live_state",
            field=models.CharField(
                choices=[("not_deployed", "Not Deployed"), ("deployed", "Deployed")],
                default="not_deployed",
                max_length=20,
            ),
        ),
    ]
