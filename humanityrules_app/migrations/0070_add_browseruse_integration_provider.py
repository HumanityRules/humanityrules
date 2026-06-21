from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0069_appdailycost_costrefreshjob"),
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
                    ("x", "X"),
                    ("browseruse", "Browser Use"),
                ],
                max_length=50,
            ),
        ),
    ]
