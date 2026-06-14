from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devopshero_app', '0067_rename_openai_provider_slug_to_openai_api'),
    ]

    operations = [
        migrations.AlterField(
            model_name='integrationconfig',
            name='provider',
            field=models.CharField(choices=[('google', 'Google'), ('x', 'X')], max_length=50, unique=True),
        ),
    ]
