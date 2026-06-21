"""Rename PolicyProxyToken -> EnvironmentBearerToken (preserves row state).

Makemigrations produced a drop-and-recreate; replaced here with
RenameModel + AlterField so existing rows survive and the
``related_name`` flips from ``policy_proxy_token`` to ``env_bearer_token``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('humanityrules_app', '0048_integrationconfig'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='PolicyProxyToken',
            new_name='EnvironmentBearerToken',
        ),
        migrations.AlterField(
            model_name='environmentbearertoken',
            name='environment',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='env_bearer_token',
                to='humanityrules_app.environment',
            ),
        ),
    ]
