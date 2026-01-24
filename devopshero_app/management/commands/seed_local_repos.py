"""
Management command to seed local repositories into the database.

Usage:
    python manage.py seed_local_repos --org=acme

Scans the deployable_repos/ folder and creates Repository records
for the specified organization.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import Organization, Repository


class Command(BaseCommand):
    help = "Create Repository records for deployable_repos/ folders"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            required=True,
            help="Organization slug to add repositories to",
        )

    def handle(self, *args, **options):
        """Scan deployable_repos/ and create Repository records."""
        org_slug = options["org"]

        # Look up organization
        try:
            organization = Organization.objects.get(slug=org_slug)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization with slug '{org_slug}' not found")

        deployable_repos_dir = settings.BASE_DIR / "deployable_repos"

        if not deployable_repos_dir.exists():
            raise CommandError(f"deployable_repos/ directory not found at {deployable_repos_dir}")

        created_count = 0
        updated_count = 0

        # Process top-level repos (skip public/ and hidden dirs)
        for entry in sorted(deployable_repos_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name == "public":
                continue

            created, updated = self._upsert_repository(
                organization=organization,
                entry=entry,
                full_name=entry.name,
            )
            created_count += created
            updated_count += updated

        # Process public/ subdirectory
        public_repos_dir = deployable_repos_dir / "public"
        if public_repos_dir.exists():
            for entry in sorted(public_repos_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name.startswith("."):
                    continue

                created, updated = self._upsert_repository(
                    organization=organization,
                    entry=entry,
                    full_name=f"public/{entry.name}",
                )
                created_count += created
                updated_count += updated

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated"
            )
        )

    def _upsert_repository(self, organization: Organization, entry: Path, full_name: str) -> tuple[int, int]:
        """Create or update a Repository record. Returns (created_count, updated_count)."""
        clone_url = f"file://{entry.resolve()}"

        repo, created = Repository.objects.update_or_create(
            organization=organization,
            full_name=full_name,
            defaults={
                "provider": Repository.Provider.LOCAL,
                "integration": None,
                "name": entry.name,
                "clone_url": clone_url,
                "default_branch": "main",
            },
        )

        action = "Created" if created else "Updated"
        self.stdout.write(f"  {action}: {full_name} -> {clone_url}")

        return (1, 0) if created else (0, 1)
