"""Rename GitProviderIntegration -> IntegrationGitProvider (preserves row state).

Done by hand (rather than via makemigrations) so the operation is a
RenameModel + AlterModelOptions pair — existing rows survive, the admin
verbose_name follows the new class name, and the rename groups this model
with the rest of the ``Integration*`` cluster (IntegrationConfig,
IntegrationGitProvider, IntegrationUserGrant) in admin and import lists.

The Repository.integration ForeignKey target is updated automatically by
RenameModel, since Django stores it as ``app_label.model_name`` — no
explicit AlterField is needed for the ``related_name="repositories"`` since
that doesn't change. The ``related_name="git_integrations"`` on the
organization FK is intentionally preserved (it's a semantic accessor name,
unused as a reverse accessor today, and the URL/view names rely on the
``git-integrations`` slug).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('humanityrules_app', '0051_rename_userthirdpartyintegration_to_integrationusergrant'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='GitProviderIntegration',
            new_name='IntegrationGitProvider',
        ),
        migrations.AlterModelOptions(
            name='integrationgitprovider',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Integration Git Provider',
                'verbose_name_plural': 'Integration Git Providers',
            },
        ),
    ]
