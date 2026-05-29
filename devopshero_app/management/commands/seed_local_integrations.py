"""Seed the DB rows the local Hermes compose stack needs for integrations.

The integrations broker (`integrations_broker.py`) only starts when supervisor
sees DOH_CONTROL_PLANE_URL / DOH_ENV_BEARER / DOH_OWNER_USERNAME / DOH_APP_SLUG.
Locally those point the broker at the laptop's Django (via the devopshero.ngrok.io
tunnel) and identify it as one existing App owned by one existing user.

Two control-plane checks then have to pass for "Connect Google" to work:

1. The OAuth start view resolves the WebUI return URL (`rd`) to an Environment
   by suffix-matching its host against `shared_alb_hosted_zone`. The local WebUI
   is http://localhost:8788, so we need an Environment whose zone is `localhost`.
2. The token-refresh + OAuth-start views resolve the env from the bearer and the
   app from `app_slug` (must be owned by `owner_username` in the env's org).

This command creates that Environment (status READY so the provisioning
job_worker, which polls PENDING, leaves it alone — no CloudFormation) and mints
an EnvironmentBearerToken with a known raw value. It does NOT create the App,
owner tag, or user: those are real rows you already own (e.g. hermes-vmendi00 /
vmendi@gmail.com). Pass --app-slug / --owner-username only to validate they
exist and are correctly wired before you start the stack.

Usage:
    uv run manage.py seed_local_integrations \\
        --aws-account "Humanity Rules Sandbox" \\
        --app-slug hermes-vmendi00 \\
        --owner-username vmendi@gmail.com
"""

import hashlib

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import (
    App,
    AWSAccount,
    Environment,
    EnvironmentBearerToken,
    ResourceTag,
    User,
)

DEFAULT_ENV_SLUG = "local"
DEFAULT_HOSTED_ZONE = "localhost"
DEFAULT_BEARER = "local-dev-bearer-token"


class Command(BaseCommand):
    help = "Seed the localhost Environment + bearer the local Hermes compose stack needs for integrations."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--aws-account", required=True, help="AWS account name the env lives under (e.g. 'Humanity Rules Sandbox').")
        parser.add_argument("--app-slug", required=True, help="App slug the local container impersonates (e.g. 'hermes-vmendi00').")
        parser.add_argument("--owner-username", required=True, help="Username that owns --app-slug (e.g. 'vmendi@gmail.com').")
        parser.add_argument("--env-slug", default=DEFAULT_ENV_SLUG, help=f"Environment slug to create (default '{DEFAULT_ENV_SLUG}').")
        parser.add_argument("--hosted-zone", default=DEFAULT_HOSTED_ZONE, help=f"shared_alb_hosted_zone; must suffix-match the WebUI host (default '{DEFAULT_HOSTED_ZONE}').")
        parser.add_argument("--bearer", default=DEFAULT_BEARER, help=f"Raw bearer to export as DOH_ENV_BEARER (default '{DEFAULT_BEARER}').")
        parser.add_argument("--region", default="us-east-1", help="aws_region for the env row (cosmetic locally; default 'us-east-1').")

    def handle(self, *args, **options) -> None:
        aws_account = self._resolve_aws_account(name=options["aws_account"])
        env = self._ensure_environment(
            aws_account=aws_account,
            env_slug=options["env_slug"],
            hosted_zone=options["hosted_zone"],
            region=options["region"],
        )
        self._validate_app_and_owner(
            org=aws_account.organization,
            app_slug=options["app_slug"],
            owner_username=options["owner_username"],
        )
        raw = options["bearer"]
        self._mint_bearer(env=env, raw=raw)
        self._print_summary(env=env, app_slug=options["app_slug"], owner_username=options["owner_username"], raw=raw)

    def _resolve_aws_account(self, name: str) -> AWSAccount:
        """Return the AWSAccount by name, or fail with the available choices."""
        try:
            return AWSAccount.objects.select_related("organization").get(name=name)
        except AWSAccount.DoesNotExist:
            available = ", ".join(sorted(a.name for a in AWSAccount.objects.all())) or "(none)"
            raise CommandError(f"No AWSAccount named {name!r}. Available: {available}")

    def _ensure_environment(self, aws_account: AWSAccount, env_slug: str, hosted_zone: str, region: str) -> Environment:
        """Create or update the local Environment as READY so the provisioning worker ignores it."""
        env, created = Environment.objects.update_or_create(
            aws_account=aws_account,
            slug=env_slug,
            defaults={
                "name": env_slug,
                "aws_region": region,
                "shared_alb_hosted_zone": hosted_zone,
                "status": Environment.Status.READY,
                "status_message": "Local integrations dev env (seed_local_integrations); not provisioned.",
            },
        )
        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"{action} Environment slug={env_slug!r} zone={hosted_zone!r} status=READY"))
        return env

    def _validate_app_and_owner(self, org, app_slug: str, owner_username: str) -> None:
        """Fail loudly now if the App / owner-tag / user wiring won't satisfy the OAuth start view later."""
        app = App.objects.filter(organization=org, slug=app_slug).first()
        if app is None:
            raise CommandError(f"No App slug={app_slug!r} in org {org.name!r}. The local container can only impersonate an existing app.")

        owner_tag = ResourceTag.objects.filter(
            resource_type=ResourceTag.ResourceType.APP,
            app=app,
            key="owner",
            value=owner_username,
        ).first()
        if owner_tag is None:
            raise CommandError(f"App {app_slug!r} has no owner tag for {owner_username!r}; OAuth start would reject the app_slug.")

        user = User.objects.filter(username=owner_username, organization_memberships__organization=org).first()
        if user is None:
            raise CommandError(f"No User {owner_username!r} in org {org.name!r}; token refresh would treat every provider as absent.")

        self.stdout.write(self.style.SUCCESS(f"validated App={app_slug!r} owner={owner_username!r} (tag + org membership OK)"))

    def _mint_bearer(self, env: Environment, raw: str) -> None:
        """Store only the SHA-256 hash on DOH; the raw value goes in the container's DOH_ENV_BEARER."""
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        _, created = EnvironmentBearerToken.objects.update_or_create(
            environment=env,
            defaults={"token_hash": token_hash},
        )
        action = "minted" if created else "rotated"
        self.stdout.write(self.style.SUCCESS(f"{action} EnvironmentBearerToken for env={env.slug!r}"))

    def _print_summary(self, env: Environment, app_slug: str, owner_username: str, raw: str) -> None:
        """Print the exact compose env block to paste into .env."""
        self.stdout.write("")
        self.stdout.write("Set these in template_repos/hermes_agent_local/.env:")
        self.stdout.write("")
        self.stdout.write("  DOH_CONTROL_PLANE_URL=https://devopshero.ngrok.io")
        self.stdout.write(f"  DOH_ENV_BEARER={raw}")
        self.stdout.write(f"  DOH_OWNER_USERNAME={owner_username}")
        self.stdout.write(f"  DOH_APP_SLUG={app_slug}")
        self.stdout.write("")
        self.stdout.write("Then: cd template_repos/hermes_agent_local && docker compose up --build")
