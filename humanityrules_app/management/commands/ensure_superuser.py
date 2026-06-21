"""Ensure a superuser exists for initial admin access."""

import os

from django.core.management.base import BaseCommand

from humanityrules_app.models import User


class Command(BaseCommand):
    """Create or promote a user to superuser based on DJANGO_SUPERUSER_EMAIL env var."""

    help = "Ensure superuser exists for the email in DJANGO_SUPERUSER_EMAIL"

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
        if not email:
            self.stdout.write("DJANGO_SUPERUSER_EMAIL not set, skipping superuser setup")
            return

        try:
            user = User.objects.get(email=email)
            if user.is_staff and user.is_superuser:
                self.stdout.write(f"User {email} is already a superuser")
                return

            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Promoted {email} to superuser"))
        except User.DoesNotExist:
            self.stdout.write(f"User {email} does not exist yet, will be promoted on first login")
