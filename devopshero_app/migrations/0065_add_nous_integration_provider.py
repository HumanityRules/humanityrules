from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devopshero_app", "0064_add_openrouter_integration_provider"),
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
                ],
                max_length=50,
            ),
        ),
    ]
