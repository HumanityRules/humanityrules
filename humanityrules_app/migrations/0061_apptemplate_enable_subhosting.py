from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0060_integrationusercredential_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="apptemplate",
            name="enable_subhosting",
            field=models.BooleanField(default=False),
        ),
    ]
