"""
Control plane operations for environments and deployments.

Usage:
    uv run manage.py doh_control create-env --aws-account "Name" --name default --region us-east-1 --hosted-zone example.com
    uv run manage.py doh_control teardown-env --slug default --aws-account "Name"
    uv run manage.py doh_control retry-env-provisioning --slug default --aws-account "Name"
    uv run manage.py doh_control retry-app-deployment --app simple-dashboard

For production, use ./prod_manage.sh doh_control <operation> instead.

For querying data, use doh_query instead.
"""

from django.core.management.base import BaseCommand

from devopshero_app import models


class Command(BaseCommand):
    help = "Control plane operations for environments and deployments"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="operation", help="Operation to perform")

        # create-env
        create_env = subparsers.add_parser("create-env", help="Create a new environment")
        create_env.add_argument("--aws-account", required=True, help="AWS account name")
        create_env.add_argument("--name", required=True, help="Environment name")
        create_env.add_argument("--slug", help="Environment slug (defaults to name)")
        create_env.add_argument("--region", required=True, help="AWS region (e.g., us-east-1)")
        create_env.add_argument("--hosted-zone", help="Hosted zone for HTTPS (e.g., dev.example.com)")

        # teardown-env
        teardown_env = subparsers.add_parser("teardown-env", help="Tear down an environment")
        teardown_env.add_argument("--slug", required=True, help="Environment slug")
        teardown_env.add_argument("--aws-account", required=True, help="AWS account name")

        # retry-env-provisioning
        retry_env = subparsers.add_parser("retry-env-provisioning", help="Retry provisioning for a failed environment")
        retry_env.add_argument("--slug", required=True, help="Environment slug")
        retry_env.add_argument("--aws-account", required=True, help="AWS account name")

        # retry-app-deployment
        retry_deployment = subparsers.add_parser("retry-app-deployment", help="Retry a failed app deployment")
        retry_deployment.add_argument("--app", required=True, help="App slug")

    def handle(self, *args, **options):
        operation = options.get("operation")

        if operation == "create-env":
            self._handle_create_env(options)
        elif operation == "teardown-env":
            self._handle_teardown_env(options)
        elif operation == "retry-env-provisioning":
            self._handle_retry_env_provisioning(options)
        elif operation == "retry-app-deployment":
            self._handle_retry_app_deployment(options)
        else:
            self.stderr.write(self.style.ERROR("No operation specified. Use --help for usage."))

    def _handle_create_env(self, options):
        """Create a new environment and start provisioning."""
        account_name = options["aws_account"]
        name = options["name"]
        slug = options.get("slug") or name.lower().replace(" ", "-")
        region = options["region"]
        hosted_zone = options.get("hosted_zone") or ""

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

        # Create environment in PENDING status to trigger provisioning
        env = models.Environment.objects.create(
            aws_account=aws_account,
            name=name,
            slug=slug,
            aws_region=region,
            shared_alb_hosted_zone=hosted_zone,
            status=models.Environment.Status.PENDING,
            status_message="Created via doh_control command",
        )

        self.stdout.write(self.style.SUCCESS(f"\nCreated environment: {env.name} ({env.slug})"))
        self.stdout.write(f"  AWS Account: {aws_account.name}")
        self.stdout.write(f"  Region: {region}")
        self.stdout.write(f"  Hosted Zone: {hosted_zone or '(none)'}")
        self.stdout.write(f"  Status: {env.status}")
        self.stdout.write(self.style.WARNING("\nProvisioning will start automatically (job worker picks up pending environments)"))
        self.stdout.write("")

    def _handle_retry_env_provisioning(self, options):
        """Retry environment provisioning by setting status to pending."""
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
        env.status_message = f"Retry triggered (was: {old_status})"
        env.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nEnvironment '{slug}' queued for retry"))
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Provisioning will restart automatically"))
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

    def _handle_retry_app_deployment(self, options):
        """Retry a failed app deployment by setting status to pending."""
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

        self.stdout.write(self.style.SUCCESS(f"\nDeployment for '{app_slug}' queued for retry"))
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Deployment will restart automatically"))
        self.stdout.write("")
