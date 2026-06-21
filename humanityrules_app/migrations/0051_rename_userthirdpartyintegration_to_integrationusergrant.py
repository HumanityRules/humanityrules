"""Rename UserThirdPartyIntegration -> IntegrationUserGrant (preserves row state).

Done by hand (rather than via makemigrations) so the operation is a
RenameModel + AlterField pair — existing rows survive, the FK
``related_name`` on User and Environment flips from
``third_party_integrations`` to ``integration_grants``, and the admin's
verbose_name follows the new class name.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('humanityrules_app', '0050_userthirdpartyintegration'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='UserThirdPartyIntegration',
            new_name='IntegrationUserGrant',
        ),
        migrations.AlterModelOptions(
            name='integrationusergrant',
            options={
                'verbose_name': 'Integration User Grant',
                'verbose_name_plural': 'Integration User Grants',
            },
        ),
        migrations.AlterField(
            model_name='integrationusergrant',
            name='environment',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='integration_grants',
                to='humanityrules_app.environment',
            ),
        ),
        migrations.AlterField(
            model_name='integrationusergrant',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='integration_grants',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
