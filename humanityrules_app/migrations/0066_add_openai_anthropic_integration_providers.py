from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0065_add_nous_integration_provider"),
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
                    ("openai", "OpenAI"),
                    ("anthropic", "Anthropic"),
                ],
                max_length=50,
            ),
        ),
    ]
