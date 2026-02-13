"""
Direct CDK deployment command, bypassing the normal UI/DB/job-worker flow.

Usage:
    # Base layer (VPC + ECS cluster)
    uv run manage.py doh_raw --base --account "Humanity Rules Sandbox"
    uv run manage.py doh_raw --base --account "Humanity Rules Sandbox" --teardown
    uv run manage.py doh_raw --base --account "Humanity Rules Sandbox" --synth-only
    uv run manage.py doh_raw --base --account "Humanity Rules Sandbox" --env prod

    # Apps
    uv run manage.py doh_raw --app simple-dashboard --account "Humanity Rules Sandbox"
    uv run manage.py doh_raw --app simple-dashboard --account "Humanity Rules Sandbox" --hosted-zone dev.example.com
    uv run manage.py doh_raw --app simple-dashboard --account "Humanity Rules Sandbox" --teardown
    uv run manage.py doh_raw --app simple-dashboard --account "Humanity Rules Sandbox" --image-tag v1.2.3
    uv run manage.py doh_raw --app simple-dashboard --account "Humanity Rules Sandbox" --synth-only

Requires DOH_AWS_ACCESS_KEY and DOH_AWS_SECRET_KEY in .env (loaded via Django settings).
"""

import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import AWSAccount
from devopshero_app.services.infra_customer import deploy_app
from devopshero_app.services.infra_customer import deploy_base
from devopshero_app.services.infra_customer import example_apps
from devopshero_app.services.infra_customer import iam_utils


DEFAULT_REGION = "us-east-1"


class Command(BaseCommand):
    help = "Direct CDK deployment, bypassing UI/DB/job-worker flow"

    def add_arguments(self, parser):
        # Mutually exclusive: --base or --app
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--base",
            action="store_true",
            help="Deploy/teardown base layer (VPC + ECS cluster)",
        )
        group.add_argument(
            "--app",
            choices=list(example_apps.APP_CONFIGS.keys()),
            help="Deploy/teardown a specific app",
        )

        # Account selection (required)
        parser.add_argument(
            "--account",
            required=True,
            help="AWS account name or 12-digit account ID",
        )

        # Common options
        parser.add_argument(
            "--teardown",
            action="store_true",
            help="Teardown instead of deploy",
        )
        parser.add_argument(
            "--synth-only",
            action="store_true",
            help="Only synthesize CDK templates, don't deploy",
        )
        parser.add_argument(
            "--env",
            default="default",
            help="Environment slug (default: 'default'). Controls resource naming and isolation.",
        )

        # App-specific options
        parser.add_argument(
            "--image-tag",
            default="latest",
            help="Docker image tag (default: latest). Only used with --app",
        )
        parser.add_argument(
            "--hosted-zone",
            help="Hosted zone for HTTPS and DNS (e.g., 'dev.example.com'). Only used with --app",
        )

    def handle(self, *args, **options):
        """Execute the deployment command."""
        # Validate args
        if options["image_tag"] != "latest" and options["base"]:
            raise CommandError("--image-tag is only valid with --app")
        if options["hosted_zone"] and options["base"]:
            raise CommandError("--hosted-zone is only valid with --app")

        if options["synth_only"] and options["teardown"]:
            raise CommandError("--synth-only and --teardown are mutually exclusive")

        # Validate credentials from Django settings
        access_key = settings.DOH_AWS_ACCESS_KEY
        secret_key = settings.DOH_AWS_SECRET_KEY
        if not access_key or not secret_key:
            raise CommandError(
                "Missing DOH_AWS_ACCESS_KEY and/or DOH_AWS_SECRET_KEY in .env"
            )

        # Look up AWS account
        aws_account = self._get_aws_account(options["account"])

        # Get assumed role session
        session = iam_utils.get_assumed_role_session(
            access_key=access_key,
            secret_key=secret_key,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=DEFAULT_REGION,
        )

        # Dispatch
        if options["base"]:
            success = self._handle_base(
                session=session,
                env_slug=options["env"],
                teardown=options["teardown"],
                synth_only=options["synth_only"],
            )
        else:
            success = self._handle_app(
                session=session,
                account_id=aws_account.aws_account_id,
                app_name=options["app"],
                env_slug=options["env"],
                image_tag=options["image_tag"],
                hosted_zone=options["hosted_zone"],
                teardown=options["teardown"],
                synth_only=options["synth_only"],
            )

        if not success:
            sys.exit(1)

    def _get_aws_account(self, account_identifier: str) -> AWSAccount:
        """Look up AWS account by name or account ID."""
        # Try by account ID first (12 digits)
        if account_identifier.isdigit() and len(account_identifier) == 12:
            try:
                return AWSAccount.objects.get(aws_account_id=account_identifier)
            except AWSAccount.DoesNotExist:
                raise CommandError(f"AWS account with ID '{account_identifier}' not found")

        # Try by name
        try:
            return AWSAccount.objects.get(name=account_identifier)
        except AWSAccount.DoesNotExist:
            raise CommandError(f"AWS account with name '{account_identifier}' not found")
        except AWSAccount.MultipleObjectsReturned:
            raise CommandError(
                f"Multiple AWS accounts found with name '{account_identifier}'. "
                "Use the 12-digit account ID instead."
            )

    def _handle_base(self, session, env_slug: str, teardown: bool, synth_only: bool) -> bool:
        """Handle base layer deployment/teardown."""
        if teardown:
            return deploy_base.teardown(session=session, env_slug=env_slug)
        else:
            return deploy_base.deploy(
                session=session,
                env_slug=env_slug,
                synth_only=synth_only,
            )

    def _handle_app(
        self,
        session,
        account_id: str,
        app_name: str,
        env_slug: str,
        image_tag: str,
        hosted_zone: str | None,
        teardown: bool,
        synth_only: bool,
    ) -> bool:
        """Handle app deployment/teardown."""
        app_config = example_apps.get_app_config(
            app_name=app_name,
            env_slug=env_slug,
        )

        if teardown:
            return deploy_app.teardown(
                session=session,
                app_config=app_config,
                env_slug=env_slug,
            )
        else:
            return deploy_app.deploy(
                session=session,
                account_id=account_id,
                region=DEFAULT_REGION,
                app_config=app_config,
                image_tag=image_tag,
                env_slug=env_slug,
                subdomain=app_name,  # CLI uses app_name as subdomain
                synth_only=synth_only,
                shared_alb_hosted_zone=hosted_zone,
            ).success
