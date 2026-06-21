# Generated manually: rename AppRemovalJob.delete_efs_data -> delete_persistent_data.
# The flag now also covers EC2 host bind-mount data (e.g., /var/lib/humr/hermes-roots/{app_slug}),
# not just EFS app data, so the broader name reflects the broader semantics.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('humanityrules_app', '0053_alter_integrationconfig_options'),
    ]

    operations = [
        migrations.RenameField(
            model_name='appremovaljob',
            old_name='delete_efs_data',
            new_name='delete_persistent_data',
        ),
    ]
