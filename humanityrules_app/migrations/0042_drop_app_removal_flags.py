"""Drop the two per-attempt removal job inputs.

Removal is unconditional now: it always tears down whatever infrastructure the
app has and always purges its data, so there is nothing left for `queue_removal`
to record on the row. Schema-only — the columns were written at admission and
read once by the executor, never outside an in-flight removal, so there is no
data to migrate.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("humanityrules_app", "0041_waitlistsignup_invitation_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="app",
            name="removal_delete_all_data",
        ),
        migrations.RemoveField(
            model_name="app",
            name="removal_teardown_first",
        ),
    ]
