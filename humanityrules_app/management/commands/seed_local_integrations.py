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
job_worker, which polls PENDING, leaves it alone — no CloudFormation), mints
an EnvironmentBearerToken with a known raw value, and ensures a DB-only App
stub (App row + owner tag) for the local compose container to impersonate.
No AWS deployment or Blueprint is created — the running docker-compose stack
*is* the app; DOH only needs the identity rows for integrations auth.

Usage:
    uv run manage.py seed_local_integrations \\
        --aws-account "CH Sandbox" \\
        --app-slug hermes-vmendi00 \\
        --owner-username vmendi@gmail.com
"""

import hashlib

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import (
    App,
    AppTemplate,
    AWSAccount,
    Environment,
    EnvironmentBearerToken,
    Repository,
    ResourceTag,
    User,
    Workspace,
)

DEFAULT_ENV_SLUG = "local"
DEFAULT_HOSTED_ZONE = "localhost"
DEFAULT_BEARER = "local-dev-bearer-token"
DEFAULT_TEMPLATE_SLUG = "hermes-personal"
DEFAULT_WORKSPACE_SLUG = "default"


class Command(BaseCommand):
    help = "Seed the localhost Environment + bearer the local Hermes compose stack needs for integrations."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--aws-account", required=True, help="AWS account name the env lives under (e.g. 'CH Sandbox').")
        parser.add_argument("--app-slug", required=True, help="App slug the local container impersonates (e.g. 'hermes-vmendi00').")
        parser.add_argument("--owner-username", required=True, help="Username that owns --app-slug (e.g. 'vmendi@gmail.com').")
        parser.add_argument("--env-slug", default=DEFAULT_ENV_SLUG, help=f"Environment slug to create (default '{DEFAULT_ENV_SLUG}').")
        parser.add_argument("--hosted-zone", default=DEFAULT_HOSTED_ZONE, help=f"shared_alb_hosted_zone; must suffix-match the WebUI host (default '{DEFAULT_HOSTED_ZONE}').")
        parser.add_argument("--bearer", default=DEFAULT_BEARER, help=f"Raw bearer to export as DOH_ENV_BEARER (default '{DEFAULT_BEARER}').")
        parser.add_argument("--region", default="us-east-1", help="aws_region for the env row (cosmetic locally; default 'us-east-1').")
        parser.add_argument(
            "--template",
            default=DEFAULT_TEMPLATE_SLUG,
            help=f"AppTemplate slug for a new local App stub (default '{DEFAULT_TEMPLATE_SLUG}').",
        )
        parser.add_argument(
            "--workspace",
            default=DEFAULT_WORKSPACE_SLUG,
            help=f"Workspace slug for a new local App stub (default '{DEFAULT_WORKSPACE_SLUG}').",
        )

    def handle(self, *args, **options) -> None:
        aws_account = self._resolve_aws_account(name=options["aws_account"])
        env = self._ensure_environment(
            aws_account=aws_account,
            env_slug=options["env_slug"],
            hosted_zone=options["hosted_zone"],
            region=options["region"],
        )
        self._ensure_local_app_stub(
            org=aws_account.organization,
            app_slug=options["app_slug"],
            owner_username=options["owner_username"],
            template_slug=options["template"],
            workspace_slug=options["workspace"],
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

    def _ensure_local_app_stub(
        self,
        org,
        app_slug: str,
        owner_username: str,
        template_slug: str,
        workspace_slug: str,
    ) -> None:
        """Ensure the local compose container can impersonate *app_slug* in *org*."""
        user = User.objects.filter(username=owner_username, organization_memberships__organization=org).first()
        if user is None:
            raise CommandError(f"No User {owner_username!r} in org {org.name!r}; token refresh would treat every provider as absent.")

        app = App.objects.filter(organization=org, slug=app_slug).first()
        if app is None:
            app = self._create_local_app_stub(
                org=org,
                app_slug=app_slug,
                template_slug=template_slug,
                workspace_slug=workspace_slug,
                created_by=user,
            )
            self.stdout.write(self.style.SUCCESS(f"created local App stub slug={app_slug!r} (no deployment)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"found existing App slug={app_slug!r}"))

        owner_tag, created = ResourceTag.objects.get_or_create(
            organization=org,
            resource_type=ResourceTag.ResourceType.APP,
            app=app,
            key="owner",
            defaults={"value": owner_username},
        )
        if not created and owner_tag.value != owner_username:
            raise CommandError(
                f"App {app_slug!r} is owned by {owner_tag.value!r}, not {owner_username!r}; "
                "pick a different --app-slug or fix the owner tag.",
            )
        if created:
            self.stdout.write(self.style.SUCCESS(f"stamped owner tag {owner_username!r} on App={app_slug!r}"))

        self.stdout.write(self.style.SUCCESS(f"validated App={app_slug!r} owner={owner_username!r} (tag + org membership OK)"))

    def _create_local_app_stub(
        self,
        org,
        app_slug: str,
        template_slug: str,
        workspace_slug: str,
        created_by: User,
    ) -> App:
        """Create a DB-only Hermes App row the local compose stack impersonates."""
        try:
            template = AppTemplate.objects.get(slug=template_slug, is_active=True)
        except AppTemplate.DoesNotExist:
            raise CommandError(f"AppTemplate {template_slug!r} not found or not active; run seed_app_templates first.")

        try:
            workspace = Workspace.objects.get(organization=org, slug=workspace_slug)
        except Workspace.DoesNotExist:
            raise CommandError(f"No Workspace slug={workspace_slug!r} in org {org.name!r}.")

        primary = self._primary_build_container(template=template)
        clone_url = f"doh-template://{primary['source_repo_path']}"
        repo, _created = Repository.objects.get_or_create(
            organization=org,
            full_name=f"template/{template.slug}",
            defaults={
                "provider": Repository.Provider.LOCAL,
                "integration": None,
                "name": template.name,
                "clone_url": clone_url,
                "default_branch": "main",
            },
        )
        app = App.objects.create(
            organization=org,
            workspace=workspace,
            repository=repo,
            source_template=template,
            name=app_slug.replace("-", " ").title(),
            slug=app_slug,
            app_type=App.AppType.WEB,
            build_strategy=App.BuildStrategy.DOCKERFILE,
            dockerfile_path=primary.get("dockerfile_path", ""),
            container_port=primary["container_port"],
            health_check_path=primary.get("health_check_path", ""),
            health_check_command=primary.get("health_check_command", ""),
            health_check_grace_period=primary.get("health_check_grace_period", 0),
            branch="",
            created_by=created_by,
        )
        for tag in template.default_tags or []:
            ResourceTag.objects.get_or_create(
                organization=org,
                resource_type=ResourceTag.ResourceType.APP,
                app=app,
                key=tag["key"],
                defaults={"value": tag["value"]},
            )
        return app

    def _primary_build_container(self, template: AppTemplate) -> dict:
        """Return the template's dockerfile-built app container (not the policy proxy)."""
        target_name = template.alb_target_container
        target = next((c for c in template.containers if c["name"] == target_name), None)
        if target is None:
            raise CommandError(f"Template {template.slug!r} has no alb_target_container {target_name!r}.")
        if target["image_source"] == "policy_proxy":
            upstream = target.get("upstream_container")
            if not upstream:
                raise CommandError(f"Template {template.slug!r} policy proxy has no upstream_container.")
            upstream_container = next((c for c in template.containers if c["name"] == upstream), None)
            if upstream_container is None:
                raise CommandError(f"Template {template.slug!r} upstream_container {upstream!r} not found.")
            return upstream_container
        return target

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
