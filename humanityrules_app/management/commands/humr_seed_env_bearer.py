"""Mint an EnvironmentBearerToken row with a known raw token.

Used for local Layer-2 testing of env-resident components (the Hermes Google
refresher, the policy proxy's PDP calls, etc.) without having to go through
the full env-provisioning flow that writes the token into a customer AWS
Secrets Manager.

The normal prod path is ``ensure_env_bearer_token_exists``, which generates a
random raw token, writes it into the env's ``shared-secrets``, and stores
only the hash on DOH. That path is great for prod but annoying for local
testing because the raw token lives only in AWS. This command does the
reverse: you *pick* a raw token, DOH hashes and stores it, and you use that
same raw value in your local refresher's ``HUMR_ENV_BEARER`` env var.

Usage:
    uv run manage.py humr_seed_env_bearer --env default --token local-dev-bearer-token

If ``--token`` is omitted, a 64-char random token is generated and printed.
"""

import hashlib
import secrets

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import Environment, EnvironmentBearerToken


class Command(BaseCommand):
    help = "Mint an EnvironmentBearerToken with a chosen raw value (local Layer-2 testing)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--env",
            required=True,
            help="Environment slug (e.g. 'default', 'staging').",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="Raw bearer token to use. Omit to generate a random 64-char one.",
        )

    def handle(self, *args, **options) -> None:
        env_slug = options["env"]
        try:
            env = Environment.objects.get(slug=env_slug)
        except Environment.DoesNotExist:
            raise CommandError(f"No Environment with slug='{env_slug}' (try 'default')")

        raw = options["token"] or secrets.token_urlsafe(48)[:64]
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        row, created = EnvironmentBearerToken.objects.update_or_create(
            environment=env,
            defaults={"token_hash": token_hash},
        )

        action = "created" if created else "rotated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} EnvironmentBearerToken for env='{env_slug}'"
        ))
        self.stdout.write("")
        self.stdout.write(f"  Raw token (export as HUMR_ENV_BEARER): {raw}")
        self.stdout.write(f"  Hash stored in DB:                   {token_hash}")
