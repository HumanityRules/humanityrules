"""Idempotently bootstrap the shared Humanity Rules sandbox.

Safe to run on every deploy (e.g. from the migrate init container). Does nothing unless
HUMR_SANDBOX_AWS_ACCOUNT_ID + HUMR_SANDBOX_EXTERNAL_ID are set. When configured it:
1. Backfills a connected sandbox account + ready environment for every existing org.
2. Verifies the one shared base infra (humr-sandbox-* VPC/cluster/ALB/EFS) exists.

The base infra is heavy and slow to deploy, so the default run only VERIFIES it (and warns
if missing) — never blocks a deploy. Pass --provision-base to create or update it; run that
manually whenever the base stacks change (e.g. prod_manage.sh humr_bootstrap_sandbox --provision-base).

The assume-role the CP uses (humr-{external_id}) is created by the CDK SandboxStack, not here.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from humanityrules_app.models import Organization
from humanityrules_app.services import infra_customer, sandbox_service
from humanityrules_app.services.infra_customer import cloudformation_utils
from humanityrules_app.services.sandbox_service import HUMR_SANDBOX_ENV_SLUG


class Command(BaseCommand):
    help = "Ensure the shared Humanity Rules sandbox base infra and per-org rows exist."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--provision-base",
            action="store_true",
            help="Create or update the shared base infra (slow; run manually).",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not sandbox_service.is_sandbox_configured():
            self.stdout.write("Sandbox not configured (HUMR_SANDBOX_* unset), skipping.")
            return

        # Backfill first: it's cheap, safe, and independent of the base infra.
        self._backfill_org_rows()
        self._handle_base_infra(provision=bool(options["provision_base"]))

    def _backfill_org_rows(self) -> None:
        """Create the sandbox account + environment for any org that lacks them."""
        count = 0
        for organization in Organization.objects.all():
            try:
                sandbox_service.ensure_org_sandbox(organization=organization)
                count += 1
            except Exception as e:
                self.stderr.write(f"Failed to ensure sandbox rows for org '{organization.slug}': {e}")
        self.stdout.write(self.style.SUCCESS(f"Ensured sandbox rows for {count} organization(s)."))

    def _handle_base_infra(self, provision: bool) -> None:
        """Verify the shared base infra under slug 'sandbox'; with provision=True, create or update it."""
        cluster_stack_name = f"humr-{HUMR_SANDBOX_ENV_SLUG}-cluster"
        try:
            session = infra_customer.iam_utils.get_assumed_role_session(
                access_key=settings.HUMR_AWS_ACCESS_KEY,
                secret_key=settings.HUMR_AWS_SECRET_KEY,
                account_id=settings.HUMR_SANDBOX_AWS_ACCOUNT_ID,
                external_id=settings.HUMR_SANDBOX_EXTERNAL_ID,
                region=settings.HUMR_SANDBOX_REGION,
            )
            if not provision:
                cf_client = session.client("cloudformation")
                if cloudformation_utils.stack_exists(cf_client, stack_name=cluster_stack_name):
                    self.stdout.write(f"Shared sandbox base infra present ({cluster_stack_name}).")
                else:
                    self.stderr.write(
                        f"Shared sandbox base infra is MISSING ({cluster_stack_name}). "
                        "Run: manage.py humr_bootstrap_sandbox --provision-base"
                    )
                return

            self.stdout.write("Applying shared sandbox base infra (slow)...")
            # SANDBOX_ENI_TRUNKING_ENABLED is the single trunking switch: enforce
            # the ECS account setting here so it can't desync from the flag. It's
            # per account+region and only affects instances at registration time.
            trunking_value = "enabled" if sandbox_service.SANDBOX_ENI_TRUNKING_ENABLED else "disabled"
            session.client("ecs").put_account_setting_default(name="awsvpcTrunking", value=trunking_value)
            self.stdout.write(f"ECS awsvpcTrunking account setting: {trunking_value}.")
            success = infra_customer.deploy_base.deploy(
                session=session,
                env_slug=HUMR_SANDBOX_ENV_SLUG,
                synth_only=False,
                shared_alb_hosted_zone=settings.HUMR_SANDBOX_HOSTED_ZONE or None,
                eni_trunking_enabled=sandbox_service.SANDBOX_ENI_TRUNKING_ENABLED,
            )
            if success:
                self.stdout.write(self.style.SUCCESS("Shared sandbox base infra applied."))
            else:
                self.stderr.write("Shared sandbox base infra apply failed. Check CloudFormation.")
        except Exception as e:
            # Never let sandbox bootstrap break the deploy that runs this command.
            self.stderr.write(f"Sandbox base infra step errored (continuing): {e}")
