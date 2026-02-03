"""
Management command for customer account operations.

Usage (via prod_manage.sh):
    ./prod_manage.sh doh_customer list-infra
    ./prod_manage.sh doh_customer create-env --aws-account "Name" --name default --region us-east-1 --hosted-zone example.com
    ./prod_manage.sh doh_customer provision-env --slug default --aws-account "Name"
    ./prod_manage.sh doh_customer teardown-env --slug default --aws-account "Name"
    ./prod_manage.sh doh_customer list-apps
    ./prod_manage.sh doh_customer list-deployments
    ./prod_manage.sh doh_customer deployment-logs                    # most recent deployment globally
    ./prod_manage.sh doh_customer deployment-logs --app simple-dashboard
    ./prod_manage.sh doh_customer retry-deployment --app simple-dashboard
"""

from django.core.management.base import BaseCommand

from devopshero_app import models


class Command(BaseCommand):
    help = "Manage customer AWS accounts and environments"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="operation", help="Operation to perform")

        # list-infra
        subparsers.add_parser("list-infra", help="List all organizations, AWS accounts, and environments")

        # create-env
        create_env = subparsers.add_parser("create-env", help="Create a new environment")
        create_env.add_argument("--aws-account", required=True, help="AWS account name")
        create_env.add_argument("--name", required=True, help="Environment name")
        create_env.add_argument("--slug", help="Environment slug (defaults to name)")
        create_env.add_argument("--region", required=True, help="AWS region (e.g., us-east-1)")
        create_env.add_argument("--hosted-zone", help="Hosted zone for HTTPS (e.g., dev.example.com)")
        create_env.add_argument(
            "--provision",
            action="store_true",
            help="Set status to pending to trigger provisioning",
        )

        # provision-env
        provision_env = subparsers.add_parser("provision-env", help="Trigger environment provisioning")
        provision_env.add_argument("--slug", required=True, help="Environment slug")
        provision_env.add_argument("--aws-account", required=True, help="AWS account name")

        # list-apps
        subparsers.add_parser("list-apps", help="List all apps")

        # list-deployments
        list_deployments = subparsers.add_parser("list-deployments", help="List recent deployments")
        list_deployments.add_argument("--limit", type=int, default=10, help="Number of deployments to show")

        # deployment-logs
        deployment_logs = subparsers.add_parser("deployment-logs", help="Show logs for latest deployment")
        deployment_logs.add_argument("--app", help="App slug (if omitted, shows most recent deployment globally)")
        deployment_logs.add_argument("--limit", type=int, default=20, help="Number of log entries to show")

        # retry-deployment
        retry_deployment = subparsers.add_parser("retry-deployment", help="Retry a failed deployment")
        retry_deployment.add_argument("--app", required=True, help="App slug")

        # teardown-env
        teardown_env = subparsers.add_parser("teardown-env", help="Tear down an environment (deletes all deployments and infrastructure)")
        teardown_env.add_argument("--slug", required=True, help="Environment slug")
        teardown_env.add_argument("--aws-account", required=True, help="AWS account name")

    def handle(self, *args, **options):
        operation = options.get("operation")

        if operation == "list-infra":
            self._handle_list_infra()
        elif operation == "create-env":
            self._handle_create_env(options)
        elif operation == "provision-env":
            self._handle_provision_env(options)
        elif operation == "list-apps":
            self._handle_list_apps()
        elif operation == "list-deployments":
            self._handle_list_deployments(options)
        elif operation == "deployment-logs":
            self._handle_deployment_logs(options)
        elif operation == "retry-deployment":
            self._handle_retry_deployment(options)
        elif operation == "teardown-env":
            self._handle_teardown_env(options)
        else:
            self.stderr.write(self.style.ERROR("No operation specified. Use --help for usage."))

    def _handle_list_infra(self):
        """List all organizations, AWS accounts, and environments."""
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Organizations ==="))
        for org in models.Organization.objects.all():
            self.stdout.write(f"  {org.slug}: {org.name}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== AWS Accounts ==="))
        for account in models.AWSAccount.objects.select_related("organization").all():
            self.stdout.write(
                f"  [{account.organization.slug}] {account.name}\n"
                f"      aws_id={account.aws_account_id} status={account.status}\n"
                f"      external_id={account.external_id}"
            )

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Environments ==="))
        envs = models.Environment.objects.select_related("aws_account", "aws_account__organization").all()
        if not envs.exists():
            self.stdout.write("  (none)")
        for env in envs:
            self.stdout.write(
                f"  [{env.aws_account.organization.slug}/{env.aws_account.name}] {env.name} ({env.slug})\n"
                f"      region={env.aws_region} status={env.status}\n"
                f"      hosted_zone={env.shared_alb_hosted_zone or '(none)'}"
            )

        self.stdout.write("")

    def _handle_create_env(self, options):
        """Create a new environment."""
        account_name = options["aws_account"]
        name = options["name"]
        slug = options.get("slug") or name.lower().replace(" ", "-")
        region = options["region"]
        hosted_zone = options.get("hosted_zone") or ""
        provision = options.get("provision", False)

        # Find AWS account
        try:
            aws_account = models.AWSAccount.objects.get(name=account_name)
        except models.AWSAccount.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"AWS account '{account_name}' not found"))
            return

        # Check if environment already exists
        if models.Environment.objects.filter(aws_account=aws_account, slug=slug).exists():
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' already exists for this account"))
            return

        # Create environment
        status = models.Environment.Status.PENDING if provision else models.Environment.Status.PENDING
        env = models.Environment.objects.create(
            aws_account=aws_account,
            name=name,
            slug=slug,
            aws_region=region,
            shared_alb_hosted_zone=hosted_zone,
            status=status,
            status_message="Created via doh_customer command",
        )

        self.stdout.write(self.style.SUCCESS(f"\nCreated environment: {env.name} ({env.slug})"))
        self.stdout.write(f"  AWS Account: {aws_account.name}")
        self.stdout.write(f"  Region: {region}")
        self.stdout.write(f"  Hosted Zone: {hosted_zone or '(none)'}")
        self.stdout.write(f"  Status: {env.status}")

        if provision:
            self.stdout.write(self.style.WARNING("\nProvisioning will start automatically (job worker picks up pending environments)"))
        else:
            self.stdout.write(self.style.NOTICE("\nTo trigger provisioning, run:"))
            self.stdout.write(f"  ./prod_manage.sh doh_customer provision-env --slug {slug} --aws-account \"{account_name}\"")

        self.stdout.write("")

    def _handle_provision_env(self, options):
        """Trigger environment provisioning by setting status to pending."""
        slug = options["slug"]
        account_name = options["aws_account"]

        # Find AWS account
        try:
            aws_account = models.AWSAccount.objects.get(name=account_name)
        except models.AWSAccount.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"AWS account '{account_name}' not found"))
            return

        # Find environment
        try:
            env = models.Environment.objects.get(aws_account=aws_account, slug=slug)
        except models.Environment.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' not found for account '{account_name}'"))
            return

        if env.status == models.Environment.Status.PENDING:
            self.stdout.write(self.style.WARNING(f"Environment '{slug}' is already pending"))
            return

        if env.status == models.Environment.Status.PROVISIONING:
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' is already being provisioned"))
            return

        # Set to pending
        old_status = env.status
        env.status = models.Environment.Status.PENDING
        env.status_message = f"Re-provisioning triggered (was: {old_status})"
        env.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nEnvironment '{slug}' set to PENDING"))
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Provisioning will start automatically (job worker picks up pending environments)"))
        self.stdout.write("")

    def _handle_list_apps(self):
        """List all apps."""
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Apps ==="))
        apps = models.App.objects.select_related("workspace", "workspace__organization", "repository").all()
        if not apps.exists():
            self.stdout.write("  (none)")
        for app in apps:
            self.stdout.write(
                f"  [{app.workspace.organization.slug}/{app.workspace.slug}] {app.name} ({app.slug})\n"
                f"      repo={app.repository.full_name if app.repository else '(none)'}\n"
                f"      type={app.app_type} build={app.build_strategy}"
            )
        self.stdout.write("")

    def _handle_list_deployments(self, options):
        """List recent deployments."""
        limit = options.get("limit", 10)
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== Recent Deployments (last {limit}) ==="))
        deployments = (
            models.Deployment.objects
            .select_related("app", "environment")
            .order_by("-created_at")[:limit]
        )
        if not deployments:
            self.stdout.write("  (none)")
        for d in deployments:
            status_style = self.style.SUCCESS if d.status == "running" else (
                self.style.ERROR if d.status == "failed" else self.style.WARNING
            )
            self.stdout.write(
                f"  {d.app.slug} -> {d.environment.slug}: {status_style(d.status)}\n"
                f"      git_ref={d.git_ref} created={d.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"      message={d.status_message[:60] if d.status_message else '(none)'}"
            )
        self.stdout.write("")

    def _handle_deployment_logs(self, options):
        """Show logs for latest deployment (optionally filtered by app)."""
        app_slug = options.get("app")
        limit = options.get("limit", 20)

        # Find deployment - either by app or most recent globally
        if app_slug:
            try:
                app = models.App.objects.get(slug=app_slug)
            except models.App.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"App '{app_slug}' not found"))
                return
            deployment = models.Deployment.objects.filter(app=app).order_by("-created_at").first()
            if not deployment:
                self.stderr.write(self.style.ERROR(f"No deployments found for app '{app_slug}'"))
                return
            title = f"Deployment Logs for {app_slug}"
        else:
            deployment = models.Deployment.objects.order_by("-created_at").first()
            if not deployment:
                self.stderr.write(self.style.ERROR("No deployments found"))
                return
            title = f"Deployment Logs for {deployment.app.slug} (most recent)"

        status_style = self.style.SUCCESS if deployment.status == "running" else (
            self.style.ERROR if deployment.status == "failed" else self.style.WARNING
        )

        self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {title} ==="))
        self.stdout.write(f"  Deployment: {deployment.id}")
        self.stdout.write(f"  Status: {status_style(deployment.status)}")
        self.stdout.write(f"  Created: {deployment.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        self.stdout.write(f"  Updated: {deployment.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if deployment.status_message:
            self.stdout.write(f"  Message: {deployment.status_message[:80]}")
        self.stdout.write("")

        # Show logs in reverse chronological order (newest first)
        logs = models.DeploymentLog.objects.filter(deployment=deployment).order_by("-created_at")[:limit]
        if not logs:
            self.stdout.write("  (no logs)")
        for log in logs:
            level_style = self.style.ERROR if log.level == "error" else self.style.SUCCESS
            timestamp = log.created_at.strftime("%H:%M:%S")
            self.stdout.write(f"  [{timestamp}] [{level_style(log.level)}] {log.source}: {log.message[:120]}")
        self.stdout.write("")

    def _handle_retry_deployment(self, options):
        """Retry a failed deployment by setting status to pending."""
        app_slug = options["app"]

        # Find app
        try:
            app = models.App.objects.get(slug=app_slug)
        except models.App.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"App '{app_slug}' not found"))
            return

        # Find latest deployment
        deployment = models.Deployment.objects.filter(app=app).order_by("-created_at").first()
        if not deployment:
            self.stderr.write(self.style.ERROR(f"No deployments found for app '{app_slug}'"))
            return

        if deployment.status == models.Deployment.Status.PENDING:
            self.stdout.write(self.style.WARNING(f"Deployment is already pending"))
            return

        if deployment.status == models.Deployment.Status.RUNNING:
            self.stderr.write(self.style.ERROR(f"Deployment is already running - nothing to retry"))
            return

        if deployment.status in [
            models.Deployment.Status.BUILDING,
            models.Deployment.Status.PUSHING,
            models.Deployment.Status.DEPLOYING,
            models.Deployment.Status.STARTING,
        ]:
            self.stderr.write(self.style.ERROR(f"Deployment is in progress ({deployment.status}) - cannot retry"))
            return

        # Reset to pending
        old_status = deployment.status
        deployment.status = models.Deployment.Status.PENDING
        deployment.status_message = f"Retry triggered (was: {old_status})"
        deployment.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nDeployment for '{app_slug}' reset to PENDING"))
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Deployment will restart automatically (job worker picks up pending deployments)"))
        self.stdout.write("")

    def _handle_teardown_env(self, options):
        """Tear down an environment by setting status to TEARDOWN_PENDING."""
        slug = options["slug"]
        account_name = options["aws_account"]

        # Find AWS account
        try:
            aws_account = models.AWSAccount.objects.get(name=account_name)
        except models.AWSAccount.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"AWS account '{account_name}' not found"))
            return

        # Find environment
        try:
            env = models.Environment.objects.get(aws_account=aws_account, slug=slug)
        except models.Environment.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' not found for account '{account_name}'"))
            return

        # Check current status
        if env.status == models.Environment.Status.TEARDOWN_PENDING:
            self.stdout.write(self.style.WARNING(f"Environment '{slug}' is already queued for teardown"))
            return

        if env.status == models.Environment.Status.TEARING_DOWN:
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' is already being torn down"))
            return

        # Set to teardown pending
        old_status = env.status
        env.status = models.Environment.Status.TEARDOWN_PENDING
        env.status_message = f"Teardown triggered (was: {old_status})"
        env.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nEnvironment '{slug}' set to TEARDOWN_PENDING"))
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Teardown will start automatically (job worker picks up pending teardowns)"))
        self.stdout.write("")
