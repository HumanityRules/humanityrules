import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0031_alter_deploymentblueprint_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="deployment",
            name="blueprint",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deployments",
                to="humanityrules_app.deploymentblueprint",
            ),
        ),
    ]
