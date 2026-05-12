from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devopshero_app', '0054_rename_delete_efs_data_to_delete_persistent_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='apptemplate',
            name='serialize_task_replacement',
            field=models.BooleanField(default=False),
        ),
    ]
