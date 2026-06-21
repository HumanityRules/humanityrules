"""Delete deprecated Hermes Docker AppTemplate rows.

The `hermes-docker-personal` and `hermes-docker-slack` templates (and their
underlying `template_repos/hermes_docker_agent/` source tree) have been moved
to `template_repos/deprecated/hermes_docker_agent/` and dropped from
`seed_app_templates.py`. This migration removes any matching DB rows so
they no longer appear in the deploy UI or list queries.

Backward is a no-op: bringing the rows back requires reactivating the
template definitions in `seed_app_templates.py` and re-running
`seed_app_templates`, which is the canonical recreate path anyway.
"""

from django.db import migrations


_DEPRECATED_SLUGS = ["hermes-docker-personal", "hermes-docker-slack"]


def remove_deprecated_templates(apps, schema_editor):
    AppTemplate = apps.get_model("humanityrules_app", "AppTemplate")
    AppTemplate.objects.filter(slug__in=_DEPRECATED_SLUGS).delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0056_app_label"),
    ]

    operations = [
        migrations.RunPython(remove_deprecated_templates, noop_reverse),
    ]
