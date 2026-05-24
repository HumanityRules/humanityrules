"""Generalize per-user integration grants into logical app credentials."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import Apps
from django.utils import timezone


def copy_legacy_grant_payloads(apps: Apps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Move refresh_token/scope into JSON payload columns before dropping fields."""
    integration_user_credential = apps.get_model("devopshero_app", "IntegrationUserCredential")
    for row in integration_user_credential.objects.all().iterator():
        credentials = {}
        if row.refresh_token:
            credentials["refresh_token"] = row.refresh_token
        config = {}
        if row.scope:
            config["scope"] = row.scope
        row.credentials = credentials
        row.config = config
        row.metadata = {}
        row.save(update_fields=["credentials", "config", "metadata"])


class Migration(migrations.Migration):

    dependencies = [
        ("devopshero_app", "0059_organizationinvite"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="integrationusergrant",
            name="unique_user_env_provider",
        ),
        migrations.RenameModel(
            old_name="IntegrationUserGrant",
            new_name="IntegrationUserCredential",
        ),
        migrations.RenameField(
            model_name="integrationusercredential",
            old_name="user",
            new_name="owner_user",
        ),
        migrations.RenameField(
            model_name="integrationusercredential",
            old_name="granted_at",
            new_name="created_at",
        ),
        migrations.AlterModelOptions(
            name="integrationusercredential",
            options={
                "verbose_name": "Integration User Credential",
                "verbose_name_plural": "Integration User Credentials",
            },
        ),
        migrations.AlterField(
            model_name="integrationusercredential",
            name="owner_user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="integration_credentials",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="integrationusercredential",
            name="environment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="integration_credentials",
                to="devopshero_app.environment",
            ),
        ),
        migrations.AlterField(
            model_name="integrationusercredential",
            name="provider",
            field=models.CharField(
                choices=[
                    ("google", "Google"),
                    ("github", "GitHub"),
                    ("telegram", "Telegram"),
                ],
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="integrationusercredential",
            name="app_slug",
            field=models.SlugField(default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="integrationusercredential",
            name="credentials",
            field=models.JSONField(
                default=dict,
                help_text="Secret provider-owned values supplied by the user, such as refresh tokens or API keys.",
            ),
        ),
        migrations.AddField(
            model_name="integrationusercredential",
            name="config",
            field=models.JSONField(default=dict, help_text="Non-secret provider configuration for this logical app."),
        ),
        migrations.AddField(
            model_name="integrationusercredential",
            name="metadata",
            field=models.JSONField(
                default=dict,
                help_text="Derived display/status data such as bot usernames or granted scopes.",
            ),
        ),
        migrations.AddField(
            model_name="integrationusercredential",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.RunPython(copy_legacy_grant_payloads, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="integrationusercredential",
            name="refresh_token",
        ),
        migrations.RemoveField(
            model_name="integrationusercredential",
            name="scope",
        ),
        migrations.AlterField(
            model_name="integrationusercredential",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AddConstraint(
            model_name="integrationusercredential",
            constraint=models.UniqueConstraint(
                fields=("owner_user", "environment", "app_slug", "provider"),
                name="unique_owner_env_appslug_provider",
            ),
        ),
    ]
