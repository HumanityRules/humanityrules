from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0014_awsresourcecache"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="PermissionRequest",
            new_name="AppPermissionRequest",
        ),
    ]
