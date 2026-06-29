"""
Add (or replace) an everyone-scoped org-shared integration API key.

Prompts for the provider (unless --provider is given), then live-validates the
pasted key against the provider before saving an ``IntegrationSharedCredential``
with scope=everyone. The post_save signal seeds the ABAC tags, so every member
of the org immediately resolves this key — it shadows their own pasted key
(organization wins; see docs/shared_credentials_design.md). Re-running for the
same org+provider replaces the stored key.

Only vault key providers exposing ``validate_shared_key`` are shareable
(OpenRouter, OpenAI, Anthropic).

Usage:
    uv run manage.py humr_add_shared_key --org course-hero
    uv run manage.py humr_add_shared_key --org "Course Hero" --provider openrouter
    uv run manage.py humr_add_shared_key --org course-hero --provider anthropic --api-key sk-ant-...

Production:
    ./prod_manage.sh humr_add_shared_key --org course-hero --provider openrouter --api-key sk-or-...
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import IntegrationSharedCredential, Organization
from humanityrules_app.views.integrations import org_shared_keys, provider_registry


def _resolve_organization(org_identifier: str) -> Organization:
    """Resolve an Organization by slug or exact name."""
    org = (
        Organization.objects.filter(slug=org_identifier).first()
        or Organization.objects.filter(name=org_identifier).first()
    )
    if org is None:
        raise CommandError(f"No organization matching {org_identifier!r} found (tried slug and name).")
    return org


class Command(BaseCommand):
    help = "Add or replace an everyone-scoped org-shared integration API key (asks for the provider)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--org", required=True, help="Organization slug or name (e.g. course-hero or 'Course Hero')")
        parser.add_argument("--provider", default=None, help="Provider slug (e.g. openrouter); prompts if omitted")
        parser.add_argument("--api-key", default=None, help="API key; read from a hidden prompt if omitted")

    def handle(self, *args, **options) -> None:
        org = _resolve_organization(org_identifier=options["org"])
        choices = org_shared_keys.paste_provider_choices()
        if not choices:
            raise CommandError("No shareable providers are registered.")

        provider = options["provider"] or self._prompt_provider(choices=choices)
        valid_slugs = {choice["value"] for choice in choices}
        if provider not in valid_slugs:
            offered = ", ".join(choice["value"] for choice in choices)
            raise CommandError(f"Provider {provider!r} is not shareable. Choose one of: {offered}.")

        api_key = (options["api_key"] or self._prompt_api_key()).strip()
        if not api_key:
            raise CommandError("An API key is required.")

        spec = provider_registry.get(provider=provider)
        metadata, key_error = spec.module.validate_shared_key(api_key=api_key)
        if key_error is not None:
            raise CommandError(f"Provider rejected the key: {key_error}")

        credential, created = IntegrationSharedCredential.objects.update_or_create(
            organization=org,
            provider=provider,
            scope=IntegrationSharedCredential.Scope.EVERYONE,
            defaults={
                "credentials": {"api_key": api_key},
                "metadata": metadata,
                "target_user": None,
                "target_workspace": None,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Replaced'} everyone-scoped {provider} key for org {org.slug!r} "
            f"(validated_at={metadata.get('validated_at')})."
        ))

    def _prompt_provider(self, choices: list[dict]) -> str:
        """Show a numbered menu of shareable providers and return the chosen slug."""
        self.stdout.write("Select a provider to share with everyone:")
        for index, choice in enumerate(choices, start=1):
            self.stdout.write(f"  {index}) {choice['label']} ({choice['value']})")
        while True:
            try:
                raw = input("Provider number: ").strip()
            except EOFError:
                raise CommandError("No provider selected (no input available). Pass --provider instead.")
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]["value"]
            self.stdout.write(self.style.ERROR("Invalid selection; enter a listed number."))

    def _prompt_api_key(self) -> str:
        """Read the API key from a hidden prompt."""
        try:
            return getpass.getpass("API key (input hidden): ")
        except EOFError:
            raise CommandError("No API key provided (no input available). Pass --api-key instead.")
