"""Split the latest attempt's structured failure kind from its display text."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0043_drop_teardown_job_states"),
    ]

    operations = [
        migrations.RenameField(
            model_name="app",
            old_name="last_attempt_error",
            new_name="last_attempt_error_text",
        ),
        migrations.AddField(
            model_name="app",
            name="last_attempt_error",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("deploy_failed", "Deploy Failed"),
                    ("removal_failed", "Removal Failed"),
                ],
                default="",
                max_length=30,
            ),
        ),
    ]
