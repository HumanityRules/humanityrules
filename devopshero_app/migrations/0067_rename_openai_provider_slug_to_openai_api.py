from django.db import migrations, models


def rename_openai_to_openai_api(apps, schema_editor):
    IntegrationUserCredential = apps.get_model("devopshero_app", "IntegrationUserCredential")
    IntegrationUserCredential.objects.filter(provider="openai").update(provider="openai-api")


def rename_openai_api_to_openai(apps, schema_editor):
    IntegrationUserCredential = apps.get_model("devopshero_app", "IntegrationUserCredential")
    IntegrationUserCredential.objects.filter(provider="openai-api").update(provider="openai")


class Migration(migrations.Migration):

    dependencies = [
        ("devopshero_app", "0066_add_openai_anthropic_integration_providers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="integrationusercredential",
            name="provider",
            field=models.CharField(
                choices=[
                    ("google", "Google"),
                    ("github", "GitHub"),
                    ("telegram", "Telegram"),
                    ("slack", "Slack"),
                    ("openai-codex", "OpenAI Codex"),
                    ("openrouter", "OpenRouter"),
                    ("nous", "Nous Portal"),
                    ("openai-api", "OpenAI API"),
                    ("anthropic", "Anthropic"),
                ],
                max_length=50,
            ),
        ),
        migrations.RunPython(rename_openai_to_openai_api, rename_openai_api_to_openai),
    ]
