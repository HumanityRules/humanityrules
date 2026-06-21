"""Create or update an organization with OIDC authentication config."""

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import Organization


class Command(BaseCommand):
    help = "Create or update an organization with OIDC (Okta) authentication"

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Organization slug")
        parser.add_argument("--name", help="Organization name")
        parser.add_argument("--issuer-url", help="OIDC issuer URL (e.g. https://dev-123.okta.com/oauth2/default)")
        parser.add_argument("--client-id", help="OIDC client ID")
        parser.add_argument("--client-secret", help="OIDC client secret")
        parser.add_argument("--bootstrap-admin-email", help="Email of first admin user (triggers org bootstrap on first login)")

    def _ask(self, label, default=""):
        prompt = f"  {label}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        value = input(prompt).strip()
        return value or default

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Setup OIDC Organization"))
        self.stdout.write()

        slug = options["slug"] or self._ask("Slug")
        name = options["name"] or self._ask("Name", default=slug)
        issuer_url = options["issuer_url"] or self._ask("Issuer URL")
        client_id = options["client_id"] or self._ask("Client ID")
        client_secret = options["client_secret"] or self._ask("Client Secret")
        bootstrap_admin_email = options["bootstrap_admin_email"] or self._ask("Bootstrap admin email")

        org, created = Organization.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "auth_provider": Organization.AuthProvider.OIDC,
                "oidc_issuer_url": issuer_url,
                "oidc_client_id": client_id,
                "oidc_client_secret": client_secret,
                "bootstrap_admin_email": bootstrap_admin_email,
            },
        )

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} OIDC org '{org.name}' ({org.slug})"))
        self.stdout.write(f"  Issuer URL: {org.oidc_issuer_url}")
        self.stdout.write(f"  Client ID:  {org.oidc_client_id}")
        self.stdout.write(f"  Login URL:  /oidc/login/?org={org.slug}")
