"""
Control plane operations for environment provisioning and app deployments.

Usage:
    uv run manage.py humr_control create-env --aws-account "Name" --name default --region us-east-1 --hosted-zone example.com
    uv run manage.py humr_control teardown-env --slug default --aws-account "Name"
    uv run manage.py humr_control teardown-app --app ai-detector-and-humanizer
    uv run manage.py humr_control teardown-app --app foo --remove-app --delete-secrets --delete-persistent-data --delete-policies
    uv run manage.py humr_control deploy-app-template --template hermes-agent --org acme-corp --workspace default --env default --app-name hermes-vmendi
    uv run manage.py humr_control redeploy-env --slug default --aws-account "Name"
    uv run manage.py humr_control redeploy-app --app simple-dashboard
    uv run manage.py humr_control redeploy-app --app simple-dashboard --env default
    uv run manage.py humr_control redeploy-app --app simple-dashboard --deployment <uuid>
    uv run manage.py humr_control restart-task --app hermes-vmendi01
    uv run manage.py humr_control restart-task --app hermes-vmendi01 --env default

For production, use ./prod_manage.sh humr_control <operation> instead.

For querying data, use humr_query instead.
"""

from datetime import datetime
from uuid import UUID

from asgiref.sync import async_to_sync
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from humanityrules_app import models
from humanityrules_app.services.app_templates import template_deploy_service
from humanityrules_app.services.infra_customer import iam_utils


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

        # teardown-app
        teardown_app = subparsers.add_parser("teardown-app", help="Tear down an app's deployment (and optionally remove the app)")
        teardown_app.add_argument("--app", required=True, help="App slug")
        teardown_app.add_argument(
            "--remove-app", action="store_true",
            help="After tearing down all live deployments, also remove the app (creates an AppRemovalJob with teardown_first=True). Equivalent to the UI's 'Remove App' button.",
        )
        teardown_app.add_argument(
            "--delete-secrets", action="store_true",
            help="With --remove-app: also delete humr/{env}/{app}/* AWS Secrets Manager secrets in every env.",
        )
        teardown_app.add_argument(
            "--delete-persistent-data", action="store_true",
            help="With --remove-app: also delete the app's persistent data (EFS subtree /deployments/{app} and EC2 host bind-mount directories) in every env. No-op if the template declares neither.",
        )
        teardown_app.add_argument(
            "--delete-policies", action="store_true",
            help="With --remove-app: also delete policies targeting app-name={app}.",
        )

        # redeploy-env
        redeploy_env = subparsers.add_parser(
            "redeploy-env",
            help="Re-run CloudFormation provisioning for an environment (DRAFT, ERROR, or READY sources)",
        )
        redeploy_env.add_argument("--slug", required=True, help="Environment slug")
        redeploy_env.add_argument("--aws-account", required=True, help="AWS account name")

        # redeploy-app
        redeploy_app = subparsers.add_parser(
            "redeploy-app",
            help="Redeploy an app to the same environment (CLI parity with the UI's 'Redeploy' button)",
        )
        redeploy_app.add_argument("--app", required=True, help="App slug")
        redeploy_app.add_argument(
            "--env",
            help="Environment slug to pick the source deployment from. Required if the app has been deployed to more than one environment.",
        )
        redeploy_app.add_argument(
            "--aws-account",
            help="AWS account name (only required to disambiguate when --env exists across multiple accounts)",
        )
        redeploy_app.add_argument(
            "--deployment",
            help="Source deployment UUID. Overrides --env/--aws-account selection and clones from this exact row.",
        )
        redeploy_app.add_argument(
            "--created-by",
            help="Username to attribute the redeploy to (audit trail). Defaults to the first admin in the app's org, then any superuser.",
        )

        # restart-task
        restart_task = subparsers.add_parser(
            "restart-task",
            help=(
                "Stop the running ECS task for an app so the service respawns it. "
                "Cycles all containers in the task (ECS has no per-container restart primitive)."
            ),
        )
        restart_task.add_argument("--app", required=True, help="App slug")
        restart_task.add_argument(
            "--env",
            help="Environment slug. Required if the app is deployed to more than one environment.",
        )
        restart_task.add_argument(
            "--aws-account",
            help="AWS account name (only required to disambiguate when --env exists across multiple accounts)",
        )

        # deploy-app-template
        deploy_tpl = subparsers.add_parser(
            "deploy-app-template",
            help="Deploy a new app from an AppTemplate (CLI parity with the 'Deploy from template' UI flow)",
        )
        deploy_tpl.add_argument("--template", required=True, help="AppTemplate slug (must be is_active=True)")
        deploy_tpl.add_argument(
            "--org",
            help="Organization slug or exact name; scopes --workspace to that org (e.g. humr or 'Humanity Rules')",
        )
        deploy_tpl.add_argument(
            "--workspace",
            required=True,
            help="Workspace slug within the org (with --org) or unique workspace slug (legacy)",
        )
        deploy_tpl.add_argument("--env", required=True, help="Environment slug within the workspace's org")
        deploy_tpl.add_argument(
            "--aws-account",
            help="AWS account name (only required to disambiguate when the same env slug exists across multiple accounts)",
        )
        deploy_tpl.add_argument("--app-name", required=True, help="Display name for the new app; slug is auto-derived")
        deploy_tpl.add_argument(
            "--var", action="append", default=[], metavar="NAME=VALUE",
            help="Override a configurable variable. Repeatable. Required vars without a template default must be passed.",
        )
        deploy_tpl.add_argument(
            "--owner",
            help="Owner username; required for Personal Assistant templates (app-type=personal-assistant + policy-proxy container).",
        )
        deploy_tpl.add_argument(
            "--compute-mode",
            help="ECS compute mode override; defaults to template.default_compute_mode.",
        )
        deploy_tpl.add_argument(
            "--created-by",
            help="Username to attribute the deploy to (audit trail). Defaults to the first admin in the resolved org, then any superuser.",
        )
        deploy_tpl.add_argument(
            "--label",
            default="",
            help=(
                "Stamp App.label so a labelled run_job_worker (--label same value) is the only "
                "worker that picks up this app's deployment, redeploys, permission applies, and "
                "removal. Use in worktrees to avoid colliding with the unscoped main worker."
            ),
        )

    def handle(self, *args, **options):
        operation = options.get("operation")

        if operation == "create-env":
            self._handle_create_env(options)
        elif operation == "teardown-env":
            self._handle_teardown_env(options)
        elif operation == "teardown-app":
            self._handle_teardown_app(options)
        elif operation == "redeploy-env":
            self._handle_redeploy_env(options)
        elif operation == "redeploy-app":
            self._handle_redeploy_app(options)
        elif operation == "restart-task":
            self._handle_restart_task(options)
        elif operation == "deploy-app-template":
            self._handle_deploy_app_template(options)
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
            status_message="Created via humr_control command",
        )

        self.stdout.write(self.style.SUCCESS(f"\nCreated environment: {env.name} ({env.slug})"))
        self.stdout.write(f"  AWS Account: {aws_account.name}")
        self.stdout.write(f"  Region: {region}")
        self.stdout.write(f"  Hosted Zone: {hosted_zone or '(none)'}")
        self.stdout.write(f"  Status: {env.status}")
        self.stdout.write(self.style.WARNING("\nProvisioning will start automatically (job worker picks up pending environments)"))
        self.stdout.write("")

    def _handle_redeploy_env(self, options):
        """Re-queue an environment for CloudFormation provisioning by flipping status to PENDING.

        Allowed source statuses: DRAFT, ERROR, READY (re-converge a working env).
        Soft no-op: PENDING (already queued).
        Hard-blocked: PROVISIONING (in flight), TEARDOWN_PENDING / TEARING_DOWN
        (lifecycle conflict — would race the teardown executor on the same CFN stack),
        DISCARDED (abandoned setup draft, nothing to provision).
        """
        slug = options["slug"]
        account_name = options["aws_account"]

        try:
            aws_account = models.AWSAccount.objects.get(name=account_name)
        except models.AWSAccount.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"AWS account '{account_name}' not found"))
            return

        try:
            env = models.Environment.objects.get(aws_account=aws_account, slug=slug)
        except models.Environment.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Environment '{slug}' not found for account '{account_name}'"))
            return

        if env.status == models.Environment.Status.PENDING:
            self.stdout.write(self.style.WARNING(f"Environment '{slug}' is already queued for provisioning"))
            return

        blocked_statuses = (
            models.Environment.Status.PROVISIONING,
            models.Environment.Status.TEARDOWN_PENDING,
            models.Environment.Status.TEARING_DOWN,
            models.Environment.Status.DISCARDED,
        )
        if env.status in blocked_statuses:
            self.stderr.write(self.style.ERROR(
                f"Environment '{slug}' is in '{env.status}' state - cannot redeploy. "
                f"Wait for the current operation to complete, or recreate the environment if it was discarded."
            ))
            return

        allowed_statuses = (
            models.Environment.Status.DRAFT,
            models.Environment.Status.ERROR,
            models.Environment.Status.READY,
        )
        if env.status not in allowed_statuses:
            self.stderr.write(self.style.ERROR(
                f"Environment '{slug}' has unexpected status '{env.status}'. "
                f"Allowed source statuses: {', '.join(allowed_statuses)}"
            ))
            return

        old_status = env.status
        env.status = models.Environment.Status.PENDING
        env.status_message = f"Redeploy triggered via humr_control (was: {old_status})"
        env.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nEnvironment '{slug}' queued for redeploy"))
        self.stdout.write(f"  AWS Account: {aws_account.name}")
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Provisioning will restart automatically (job worker picks up pending environments)"))
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

    def _handle_teardown_app(self, options):
        """Tear down an app's most recent deployment, and optionally remove the app entirely."""
        app_slug = options["app"]
        remove_app = options.get("remove_app", False)
        delete_secrets = options.get("delete_secrets", False)
        delete_persistent_data = options.get("delete_persistent_data", False)
        delete_policies = options.get("delete_policies", False)

        if not remove_app and (delete_secrets or delete_persistent_data or delete_policies):
            self.stderr.write(self.style.ERROR(
                "--delete-secrets, --delete-persistent-data, --delete-policies require --remove-app"
            ))
            return

        try:
            app = models.App.objects.get(slug=app_slug)
        except models.App.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"App '{app_slug}' not found"))
            return

        if remove_app:
            self._queue_app_removal(
                app=app,
                delete_secrets=delete_secrets,
                delete_persistent_data=delete_persistent_data,
                delete_policies=delete_policies,
            )
            return

        deployment = models.Deployment.objects.filter(app=app).select_related("environment").order_by("-created_at").first()
        if not deployment:
            self.stderr.write(self.style.ERROR(f"No deployments found for app '{app_slug}'"))
            return

        if deployment.status == models.Deployment.Status.TEARDOWN_PENDING:
            self.stdout.write(self.style.WARNING("Deployment is already queued for teardown"))
            return

        if deployment.status == models.Deployment.Status.TEARING_DOWN:
            self.stderr.write(self.style.ERROR("Deployment is already being torn down"))
            return

        teardownable_statuses = [
            models.Deployment.Status.SUCCEEDED,
            models.Deployment.Status.FAILED,
        ]
        if deployment.status not in teardownable_statuses:
            self.stderr.write(self.style.ERROR(
                f"Deployment is in progress ({deployment.status}) - cannot tear down. Wait for it to complete."
            ))
            return

        old_status = deployment.status
        old_label = app.label
        with transaction.atomic():
            deployment.status = models.Deployment.Status.TEARDOWN_PENDING
            deployment.status_message = "Teardown triggered via humr_control"
            deployment.save(update_fields=["status", "status_message", "updated_at"])
            # Clear App.label so the unscoped main worker picks up the teardown.
            # The label scopes verification-time work to a specific run_job_worker;
            # by teardown time that worker is typically gone, leaving the row stranded.
            if app.label:
                app.label = ""
                app.save(update_fields=["label", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nDeployment for '{app_slug}' set to TEARDOWN_PENDING"))
        self.stdout.write(f"  App: {app.name}")
        self.stdout.write(f"  Environment: {deployment.environment.name}")
        self.stdout.write(f"  Deployment: {deployment.id}")
        self.stdout.write(f"  Previous status: {old_status}")
        if old_label:
            self.stdout.write(f"  Cleared App.label: {old_label!r} → '' (unscoped main worker will claim)")
        self.stdout.write(self.style.WARNING("Teardown will start automatically (job worker picks up pending teardowns)"))
        self.stdout.write("")

    def _queue_app_removal(self, app: models.App, delete_secrets: bool, delete_persistent_data: bool, delete_policies: bool) -> None:
        """Queue an AppRemovalJob with teardown_first=True; the worker tears down live deployments inline, then removes the app."""
        if app.status == models.App.Status.PENDING_REMOVAL:
            self.stdout.write(self.style.WARNING(f"App '{app.slug}' is already pending removal"))
            return

        old_label = app.label
        with transaction.atomic():
            job = models.AppRemovalJob.objects.create(
                organization=app.organization,
                app_id_snapshot=app.id,
                app_slug_snapshot=app.slug,
                app_name_snapshot=app.name,
                workspace_slug_snapshot=app.workspace.slug,
                delete_secrets=delete_secrets,
                delete_persistent_data=delete_persistent_data,
                delete_policies=delete_policies,
                teardown_first=True,
                created_by=None,
                status_message="Queued via humr_control teardown-app --remove-app",
            )
            app.status = models.App.Status.PENDING_REMOVAL
            # Clear App.label so the unscoped main worker picks up the removal.
            # The label scopes verification-time work to a specific run_job_worker;
            # by removal time that worker is typically gone, leaving the row stranded.
            update_fields = ["status", "updated_at"]
            if app.label:
                app.label = ""
                update_fields.append("label")
            app.save(update_fields=update_fields)

        self.stdout.write(self.style.SUCCESS(f"\nApp '{app.slug}' set to PENDING_REMOVAL"))
        self.stdout.write(f"  App: {app.name}")
        self.stdout.write(f"  Workspace: {app.workspace.name}")
        self.stdout.write(f"  Removal job: {job.id}")
        self.stdout.write(f"  teardown_first: True")
        self.stdout.write(f"  delete_secrets: {delete_secrets}")
        self.stdout.write(f"  delete_persistent_data: {delete_persistent_data}")
        self.stdout.write(f"  delete_policies: {delete_policies}")
        if old_label:
            self.stdout.write(f"  Cleared App.label: {old_label!r} → '' (unscoped main worker will claim)")
        self.stdout.write(self.style.WARNING(
            "Worker will tear down all live deployments inline, then perform cleanup + cascade delete"
        ))
        self.stdout.write("")

    def _handle_redeploy_app(self, options):
        """Redeploy an app: clone a concluded source Deployment into a new PENDING row.

        Mirrors the UI's 'Redeploy' button (`app_deployment_redeploy`): same blueprint,
        environment, subdomain, and git_ref; fresh image_tag so the build is rebuilt.
        Allowed source statuses: SUCCEEDED, FAILED, TORN_DOWN. Refuses if any deployment
        for the app is in progress, or if the app is PENDING_REMOVAL.
        """
        app_slug = options["app"]
        env_slug = options.get("env")
        aws_account_name = options.get("aws_account")
        deployment_id_str = options.get("deployment")
        created_by_username = options.get("created_by")

        try:
            app = models.App.objects.select_related("workspace", "organization").get(slug=app_slug)
        except models.App.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"App '{app_slug}' not found"))
            return

        if app.status == models.App.Status.PENDING_REMOVAL:
            self.stderr.write(self.style.ERROR(f"App '{app_slug}' is pending removal - cannot redeploy"))
            return

        if models.Deployment.objects.filter(app=app, status__in=models.Deployment.IN_PROGRESS_STATUSES).exists():
            self.stderr.write(self.style.ERROR(
                f"App '{app_slug}' has a deployment in progress - wait for it to complete before redeploying"
            ))
            return

        source = self._resolve_redeploy_source(
            app=app,
            deployment_id_str=deployment_id_str,
            env_slug=env_slug,
            aws_account_name=aws_account_name,
        )
        if source is None:
            return

        allowed_source_statuses = (
            models.Deployment.Status.SUCCEEDED,
            models.Deployment.Status.FAILED,
            models.Deployment.Status.TORN_DOWN,
        )
        if source.status not in allowed_source_statuses:
            self.stderr.write(self.style.ERROR(
                f"Source deployment status '{source.status}' is not redeployable. "
                f"Expected one of: {', '.join(allowed_source_statuses)}"
            ))
            return

        created_by = self._resolve_created_by(org=app.organization, username=created_by_username)
        if created_by is None:
            return

        git_ref = source.git_ref or app.branch
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
        image_tag = f"{app.slug}-{short_ref}-{timestamp}"

        new_deployment = models.Deployment.objects.create(
            blueprint=source.blueprint,
            app=app,
            environment=source.environment,
            subdomain=source.subdomain,
            git_ref=git_ref,
            image_tag=image_tag,
            status=models.Deployment.Status.PENDING,
            status_message="Redeploy triggered via humr_control",
            created_by=created_by,
        )

        self.stdout.write(self.style.SUCCESS(f"\nRedeploy queued for app '{app.slug}'"))
        self.stdout.write(f"  App: {app.name}")
        self.stdout.write(f"  Environment: {source.environment.name} ({source.environment.aws_account.name})")
        self.stdout.write(f"  Source deployment: {source.id} (status: {source.status})")
        self.stdout.write(f"  New deployment: {new_deployment.id}")
        self.stdout.write(f"  git_ref: {git_ref}")
        self.stdout.write(f"  image_tag: {image_tag}")
        self.stdout.write(f"  Created by: {created_by.username}")
        self.stdout.write(self.style.WARNING("Build/push/deploy will start automatically (job worker picks up pending deployments)"))
        self.stdout.write("")

    def _resolve_redeploy_source(self, app, deployment_id_str, env_slug, aws_account_name):
        """Pick the source Deployment to clone for a redeploy.

        Resolution order:
        1. --deployment <uuid> wins; must belong to `app`.
        2. Else filter by --env (and optional --aws-account) and pick the latest concluded deployment.
        3. Else if the app has been deployed to exactly one environment, use that one.
        4. Else error: ambiguous.

        Returns the Deployment, or None and writes an error to stderr.
        """
        if deployment_id_str:
            try:
                deployment_id = UUID(deployment_id_str)
            except ValueError:
                self.stderr.write(self.style.ERROR(f"--deployment '{deployment_id_str}' is not a valid UUID"))
                return None
            try:
                return models.Deployment.objects.select_related(
                    "blueprint", "environment", "environment__aws_account",
                ).get(id=deployment_id, app=app)
            except models.Deployment.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f"Deployment '{deployment_id_str}' not found for app '{app.slug}'"
                ))
                return None

        candidates = models.Deployment.objects.select_related(
            "blueprint", "environment", "environment__aws_account",
        ).filter(app=app)

        if env_slug:
            candidates = candidates.filter(environment__slug=env_slug)
            if aws_account_name:
                candidates = candidates.filter(environment__aws_account__name=aws_account_name)

        distinct_env_ids = set(candidates.values_list("environment_id", flat=True).distinct())
        if not distinct_env_ids:
            scope = ""
            if env_slug:
                scope = f" in env '{env_slug}'"
                if aws_account_name:
                    scope += f" / account '{aws_account_name}'"
            self.stderr.write(self.style.ERROR(f"No deployments found for app '{app.slug}'{scope}"))
            return None

        if len(distinct_env_ids) > 1:
            envs = list(models.Environment.objects.filter(id__in=distinct_env_ids).select_related("aws_account"))
            if env_slug:
                account_names = sorted({e.aws_account.name for e in envs})
                self.stderr.write(self.style.ERROR(
                    f"Environment slug '{env_slug}' is ambiguous across accounts ({', '.join(account_names)}); "
                    f"pass --aws-account to disambiguate"
                ))
            else:
                env_slugs = sorted({e.slug for e in envs})
                self.stderr.write(self.style.ERROR(
                    f"App '{app.slug}' has been deployed to multiple environments ({', '.join(env_slugs)}); "
                    f"pass --env to pick one"
                ))
            return None

        source = (
            candidates.filter(status__in=models.Deployment.CONCLUDED_STATUSES)
            .order_by("-created_at")
            .first()
        )
        if source is None:
            scope = f" in env '{env_slug}'" if env_slug else ""
            self.stderr.write(self.style.ERROR(
                f"No concluded deployments found for app '{app.slug}'{scope} - nothing to redeploy from"
            ))
            return None
        return source

    def _handle_restart_task(self, options: dict) -> None:
        """Stop the running ECS task for an app so the service scheduler respawns it.

        ECS has no per-container restart primitive: exiting a non-essential container leaves
        it dead, and exiting an essential one tears down the whole task. The supported way
        to "restart" is to stop the task and let the service relaunch it — which is what
        this does. Cycles all containers in the task.
        """
        app_slug = options["app"]
        env_slug = options.get("env")
        aws_account_name = options.get("aws_account")

        try:
            app = models.App.objects.select_related("organization").get(slug=app_slug)
        except models.App.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"App '{app_slug}' not found"))
            return

        environment = self._resolve_restart_environment(
            app=app,
            env_slug=env_slug,
            aws_account_name=aws_account_name,
        )
        if environment is None:
            return

        aws_account = environment.aws_account
        cluster_name = f"humr-{environment.slug}-cluster"
        service_name = f"humr-{environment.slug}-{app.slug}"

        session = iam_utils.get_assumed_role_session(
            access_key=settings.HUMR_AWS_ACCESS_KEY,
            secret_key=settings.HUMR_AWS_SECRET_KEY,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=environment.aws_region,
        )
        ecs_client = session.client("ecs")

        try:
            list_resp = ecs_client.list_tasks(cluster=cluster_name, serviceName=service_name, desiredStatus="RUNNING")
        except ClientError as e:
            self.stderr.write(self.style.ERROR(f"Failed to list tasks for service '{service_name}': {e}"))
            return

        task_arns = list_resp.get("taskArns") or []
        if not task_arns:
            self.stderr.write(self.style.ERROR(
                f"No RUNNING tasks for service '{service_name}' in cluster '{cluster_name}'. "
                f"The service may already be cycling or have desiredCount=0."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"\nRestarting ECS task(s) for app '{app.slug}'"))
        self.stdout.write(f"  Environment: {environment.name} ({environment.slug}) / {aws_account.name}")
        self.stdout.write(f"  Cluster:     {cluster_name}")
        self.stdout.write(f"  Service:     {service_name}")
        self.stdout.write(f"  Tasks:       {len(task_arns)}")

        for task_arn in task_arns:
            task_id = task_arn.split("/")[-1]
            try:
                ecs_client.stop_task(
                    cluster=cluster_name,
                    task=task_arn,
                    reason="Restart requested via humr_control restart-task",
                )
                self.stdout.write(f"    Stopped: {task_id}")
            except ClientError as e:
                self.stderr.write(self.style.ERROR(f"    Failed to stop {task_id}: {e}"))

        self.stdout.write(self.style.WARNING(
            "ECS service scheduler will launch a replacement task automatically."
        ))
        self.stdout.write("")

    def _resolve_restart_environment(
        self,
        app: models.App,
        env_slug: str | None,
        aws_account_name: str | None,
    ) -> models.Environment | None:
        """Pick the Environment whose running task should be restarted.

        Resolves from the app's concluded deployments (SUCCEEDED / FAILED). Filters by --env
        and optional --aws-account when provided; otherwise uses the sole target environment.
        Writes an error and returns None if the selection is ambiguous or empty.
        """
        deployments = models.Deployment.objects.select_related(
            "environment", "environment__aws_account",
        ).filter(app=app, status__in=models.Deployment.CONCLUDED_STATUSES)

        if env_slug:
            deployments = deployments.filter(environment__slug=env_slug)
            if aws_account_name:
                deployments = deployments.filter(environment__aws_account__name=aws_account_name)

        env_ids = set(deployments.values_list("environment_id", flat=True).distinct())
        if not env_ids:
            scope = ""
            if env_slug:
                scope = f" in env '{env_slug}'"
                if aws_account_name:
                    scope += f" / account '{aws_account_name}'"
            self.stderr.write(self.style.ERROR(
                f"No concluded deployments found for app '{app.slug}'{scope} - nothing to restart"
            ))
            return None

        if len(env_ids) > 1:
            envs = list(models.Environment.objects.filter(id__in=env_ids).select_related("aws_account"))
            if env_slug:
                account_names = sorted({e.aws_account.name for e in envs})
                self.stderr.write(self.style.ERROR(
                    f"Environment slug '{env_slug}' is ambiguous across accounts ({', '.join(account_names)}); "
                    f"pass --aws-account to disambiguate"
                ))
            else:
                env_slugs = sorted({e.slug for e in envs})
                self.stderr.write(self.style.ERROR(
                    f"App '{app.slug}' has been deployed to multiple environments ({', '.join(env_slugs)}); "
                    f"pass --env to pick one"
                ))
            return None

        return models.Environment.objects.select_related("aws_account").get(id=env_ids.pop())

    def _handle_deploy_app_template(self, options):
        """Deploy a new app from an AppTemplate.

        Mirrors the UI's 'Deploy from template' flow: resolves template, workspace, env,
        owner, created_by, and configurable variable overrides; then calls
        template_deploy_service.deploy_from_template to create the App + Blueprint +
        Deployment chain and queue it for the job worker.
        """
        template_slug = options["template"]
        org_identifier = options.get("org")
        workspace_slug = options["workspace"]
        env_slug = options["env"]
        aws_account_name = options.get("aws_account")
        app_name = options["app_name"].strip()
        var_specs = options.get("var") or []
        owner_username = options.get("owner")
        compute_mode_override = options.get("compute_mode")
        created_by_username = options.get("created_by")

        if not app_name:
            self.stderr.write(self.style.ERROR("--app-name must not be empty"))
            return
        app_slug = slugify(app_name)
        if not app_slug:
            self.stderr.write(self.style.ERROR("--app-name must contain at least one letter or number"))
            return

        try:
            template = models.AppTemplate.objects.get(slug=template_slug, is_active=True)
        except models.AppTemplate.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"AppTemplate '{template_slug}' not found or not active"))
            return

        if org_identifier:
            org = self._resolve_organization_by_slug_or_name(identifier=org_identifier)
            if org is None:
                return
            try:
                workspace = models.Workspace.objects.select_related("organization").get(
                    organization=org,
                    slug=workspace_slug,
                )
            except models.Workspace.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f"Workspace '{workspace_slug}' not found in org '{org.name}' ({org.slug})",
                ))
                return
        else:
            qs = models.Workspace.objects.select_related("organization").filter(slug=workspace_slug)
            n = qs.count()
            if n == 0:
                self.stderr.write(self.style.ERROR(f"Workspace '{workspace_slug}' not found"))
                return
            if n > 1:
                self.stderr.write(self.style.ERROR(
                    f"Multiple workspaces have slug '{workspace_slug}'; pass --org to pick the organization.",
                ))
                return
            workspace = qs.get()
        org = workspace.organization

        env = self._resolve_environment(
            env_slug=env_slug, org=org, aws_account_name=aws_account_name,
        )
        if env is None:
            return

        if env.status != models.Environment.Status.READY:
            self.stderr.write(self.style.ERROR(
                f"Environment '{env_slug}' is not READY (current status: {env.status})"
            ))
            return

        if models.App.objects.filter(organization=org, slug=app_slug).exists():
            self.stderr.write(self.style.ERROR(
                f"An app with slug '{app_slug}' already exists in org '{org.name}'"
            ))
            return

        compute_mode = compute_mode_override or template.default_compute_mode
        if compute_mode not in models.EcsComputeMode.values:
            self.stderr.write(self.style.ERROR(
                f"Invalid --compute-mode '{compute_mode}'. Valid values: {', '.join(models.EcsComputeMode.values)}"
            ))
            return

        overrides = self._parse_var_overrides(var_specs=var_specs, template=template)
        if overrides is None:
            return

        if not self._validate_required_variables(template=template, overrides=overrides):
            return

        if self._template_requires_owner(template=template):
            if not owner_username:
                self.stderr.write(self.style.ERROR(
                    f"Template '{template.slug}' is a Personal Assistant template; --owner is required"
                ))
                return
            if not models.OrganizationMembership.objects.filter(
                organization=org, user__username=owner_username,
            ).exists():
                self.stderr.write(self.style.ERROR(
                    f"--owner '{owner_username}' is not a member of org '{org.name}'"
                ))
                return
        elif owner_username:
            self.stdout.write(self.style.WARNING(
                f"Template '{template.slug}' does not require an owner; ignoring --owner"
            ))
            owner_username = None

        created_by = self._resolve_created_by(org=org, username=created_by_username)
        if created_by is None:
            return

        label = options.get("label") or ""

        deployment = async_to_sync(template_deploy_service.deploy_from_template)(
            template=template,
            organization=org,
            workspace=workspace,
            environment=env,
            app_name=app_name,
            app_slug=app_slug,
            created_by=created_by,
            runtime_variable_overrides=overrides,
            owner_username=owner_username,
            compute_mode=compute_mode,
            label=label,
        )

        self.stdout.write(self.style.SUCCESS(f"\nDeployment queued from template '{template.slug}'"))
        self.stdout.write(f"  App: {app_name} ({app_slug})")
        self.stdout.write(f"  Workspace: {workspace.name}")
        self.stdout.write(f"  Environment: {env.name} ({env.aws_account.name})")
        self.stdout.write(f"  Compute mode: {compute_mode}")
        self.stdout.write(f"  Owner: {owner_username or '(not applicable)'}")
        self.stdout.write(f"  Created by: {created_by.username}")
        self.stdout.write(f"  Deployment id: {deployment.id}")
        self.stdout.write(f"  Variable overrides: {len(overrides)}")
        self.stdout.write(f"  Label: {label or '(none — picked up by unscoped main worker)'}")
        self.stdout.write(self.style.WARNING("Build/push/deploy will start automatically (job worker picks up pending deployments)"))
        self.stdout.write("")

    def _resolve_organization_by_slug_or_name(self, identifier) -> "models.Organization | None":
        """Resolve an Organization by slug or exact name. Returns None and writes to stderr on failure."""
        org = (
            models.Organization.objects.filter(slug=identifier).first()
            or models.Organization.objects.filter(name=identifier).first()
        )
        if org is None:
            self.stderr.write(self.style.ERROR(
                f"No organization matching {identifier!r} (tried slug and exact name).",
            ))
        return org

    def _resolve_environment(self, env_slug, org, aws_account_name):
        """Find a single READY-or-not environment by slug within `org`.

        Returns the Environment, or None and writes an error to stderr.
        Uses --aws-account to disambiguate when the slug exists in multiple accounts.
        """
        candidates = models.Environment.objects.select_related("aws_account").filter(
            slug=env_slug, aws_account__organization=org,
        )
        if aws_account_name:
            candidates = candidates.filter(aws_account__name=aws_account_name)

        results = list(candidates)
        if not results:
            scope = f" in account '{aws_account_name}'" if aws_account_name else ""
            self.stderr.write(self.style.ERROR(
                f"Environment '{env_slug}' not found in org '{org.name}'{scope}"
            ))
            return None
        if len(results) > 1:
            account_names = ", ".join(env.aws_account.name for env in results)
            self.stderr.write(self.style.ERROR(
                f"Environment slug '{env_slug}' is ambiguous across accounts ({account_names}); "
                f"pass --aws-account to disambiguate"
            ))
            return None
        return results[0]

    def _parse_var_overrides(self, var_specs, template):
        """Parse --var NAME=VALUE pairs; return dict[name] -> value, or None on error.

        Rejects unknown names (i.e., names that don't appear in any container's
        configurable_variables) so typos fail fast instead of silently no-op.
        """
        known_names = set()
        for container in template.containers or []:
            for v in container.get("configurable_variables") or []:
                known_names.add(v["name"])

        overrides: dict[str, str] = {}
        for spec in var_specs:
            if "=" not in spec:
                self.stderr.write(self.style.ERROR(
                    f"--var '{spec}' must be in NAME=VALUE form"
                ))
                return None
            name, _, value = spec.partition("=")
            name = name.strip()
            if not name:
                self.stderr.write(self.style.ERROR(f"--var '{spec}' has an empty name"))
                return None
            if name not in known_names:
                self.stderr.write(self.style.ERROR(
                    f"--var '{name}' is not a configurable variable on template '{template.slug}'. "
                    f"Known names: {', '.join(sorted(known_names)) or '(none)'}"
                ))
                return None
            overrides[name] = value
        return overrides

    def _validate_required_variables(self, template, overrides):
        """Ensure every required user-editable var has a usable value after overrides.

        Returns True if validation passed; False (and writes errors) otherwise.
        Mirrors the form's "required vars must be filled" check.
        """
        missing: list[str] = []
        for container in template.containers or []:
            for var in container.get("configurable_variables") or []:
                if not var.get("user_editable"):
                    continue
                if not var.get("required"):
                    continue
                if var["name"] in overrides:
                    if overrides[var["name"]] == "" and not var.get("allow_empty_value", False):
                        missing.append(var["name"])
                    continue
                template_value = var.get("value")
                if var.get("category") == "secret":
                    # value=None means "auto-generate"; value="" with required is still missing.
                    if template_value == "":
                        missing.append(var["name"])
                else:
                    if template_value in (None, ""):
                        missing.append(var["name"])
        if missing:
            unique_missing = sorted(set(missing))
            self.stderr.write(self.style.ERROR(
                f"Required configurable variable(s) missing a value: {', '.join(unique_missing)}. "
                f"Pass --var NAME=VALUE for each."
            ))
            return False
        return True

    def _template_requires_owner(self, template):
        """True iff the template is a Personal Assistant (policy-proxy-fronted + app-type=personal-assistant)."""
        has_policy_proxy = any(
            c.get("image_source") == "policy_proxy"
            for c in (template.containers or [])
        )
        if not has_policy_proxy:
            return False
        for tag in (template.default_tags or []):
            if tag.get("key") == "app-type" and tag.get("value") == "personal-assistant":
                return True
        return False

    def _resolve_created_by(self, org, username):
        """Resolve the User to attribute the deploy to.

        Explicit --created-by wins. Otherwise: first ADMIN membership in the org;
        falling back to any superuser. Returns None and writes an error if nothing
        usable is found.
        """
        if username:
            try:
                user = models.User.objects.get(username=username)
            except models.User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"--created-by user '{username}' not found"))
                return None
            if not models.OrganizationMembership.objects.filter(organization=org, user=user).exists():
                self.stderr.write(self.style.ERROR(
                    f"--created-by user '{username}' is not a member of org '{org.name}'"
                ))
                return None
            return user

        admin_membership = (
            models.OrganizationMembership.objects
            .filter(organization=org, role=models.OrganizationMembership.Role.ADMIN)
            .select_related("user")
            .order_by("user__username")
            .first()
        )
        if admin_membership:
            return admin_membership.user

        superuser = models.User.objects.filter(is_superuser=True).order_by("username").first()
        if superuser:
            return superuser

        self.stderr.write(self.style.ERROR(
            f"No --created-by passed and could not find an admin in org '{org.name}' or any superuser"
        ))
        return None
