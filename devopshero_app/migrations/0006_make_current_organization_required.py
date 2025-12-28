from django.db import migrations, models
import django.db.models.deletion


def populate_current_organization(apps, schema_editor):
    """
    Set current_organization for any users where it's null.
    Uses the user's first organization membership.
    """
    User = apps.get_model('devopshero_app', 'User')
    OrganizationMembership = apps.get_model('devopshero_app', 'OrganizationMembership')
    
    for user in User.objects.filter(current_organization__isnull=True):
        membership = OrganizationMembership.objects.filter(user=user).first()
        if membership:
            user.current_organization = membership.organization
            user.save(update_fields=['current_organization'])


class Migration(migrations.Migration):

    dependencies = [
        ('devopshero_app', '0005_add_current_organization_to_user'),
    ]

    operations = [
        # First, populate any null values
        migrations.RunPython(populate_current_organization, migrations.RunPython.noop),
        
        # Then make the field required
        migrations.AlterField(
            model_name='user',
            name='current_organization',
            field=models.ForeignKey(
                help_text='The organization the user is currently working in',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='current_users',
                to='devopshero_app.organization',
            ),
        ),
    ]

