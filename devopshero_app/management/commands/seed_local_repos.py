"""
Management command to seed local repositories into the database.

Usage:
    python manage.py seed_local_repos --org=acme
    python manage.py seed_local_repos --org=acme --exclude=phoenix_app,other_repo
    python manage.py seed_local_repos --org=acme --path=/custom/path/to/repos

Scans a local directory for repository folders and creates Repository records
for the specified organization.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import Organization, Repository


class Command(BaseCommand):
    help = "Create Repository records from local repository folders"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            required=True,
            help="Organization slug to add repositories to",
        )
        parser.add_argument(
            "--path",
            default=str(settings.BASE_DIR / ".." / "deployable-repos"),
            help="Path to directory containing repository folders (default: ../deployable-repos)",
        )
        parser.add_argument(
            "--exclude",
            default="",
            help="Comma-separated list of repository names to skip (e.g., phoenix_app,other_repo)",
        )

    def handle(self, *args, **options):
        """Scan repository directory and create Repository records."""
        org_slug = options["org"]
        repos_dir = Path(options["path"]).resolve()
        exclude_list = set(name.strip() for name in options["exclude"].split(",") if name.strip())

        # Look up organization
        try:
            organization = Organization.objects.get(slug=org_slug)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization with slug '{org_slug}' not found")

        if not repos_dir.exists():
            raise CommandError(f"Repository directory not found at {repos_dir}")

        if exclude_list:
            self.stdout.write(f"Excluding: {', '.join(sorted(exclude_list))}")

        created_count = 0
        updated_count = 0

        self.stdout.write(f"Scanning: {repos_dir}")

        # Process top-level repos (skip public/ and hidden dirs)
        for entry in sorted(repos_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name == "public":
                continue
            if entry.name in exclude_list:
                continue

            created, updated = self._upsert_repository(
                organization=organization,
                entry=entry,
                full_name=entry.name,
            )
            created_count += created
            updated_count += updated

        # Process public/ subdirectory
        public_repos_dir = repos_dir / "public"
        if public_repos_dir.exists():
            for entry in sorted(public_repos_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name.startswith("."):
                    continue
                if entry.name in exclude_list:
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
