"""
Control plane operations for environment provisioning and app deployments.

Usage:
    uv run manage.py doh_control create-env --aws-account "Name" --name default --region us-east-1 --hosted-zone example.com
    uv run manage.py doh_control teardown-env --slug default --aws-account "Name"
    uv run manage.py doh_control teardown-app --app ai-detector-and-humanizer
    uv run manage.py doh_control teardown-app --app foo --remove-app --delete-secrets --delete-efs-data --delete-policies
    uv run manage.py doh_control deploy-app-template --template hermes-agent --org acme-corp --workspace default --env default --app-name hermes-vmendi
    uv run manage.py doh_control retry-env-provisioning --slug default --aws-account "Name"
    uv run manage.py doh_control retry-app-deployment --app simple-dashboard

For production, use ./prod_manage.sh doh_control <operation> instead.

For querying data, use doh_query instead.
"""

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from devopshero_app import models
from devopshero_app.services.app_templates import template_deploy_service


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
            help="With --remove-app: also delete devopshero/{env}/{app}/* AWS Secrets Manager secrets in every env.",
        )
        teardown_app.add_argument(
            "--delete-efs-data", action="store_true",
            help="With --remove-app: also delete /deployments/{app} EFS data in every env (no-op if the app has no EFS config).",
        )
        teardown_app.add_argument(
            "--delete-policies", action="store_true",
            help="With --remove-app: also delete policies targeting app-name={app}.",
        )

        # retry-env-provisioning
        retry_env = subparsers.add_parser("retry-env-provisioning", help="Retry provisioning for a failed environment")
        retry_env.add_argument("--slug", required=True, help="Environment slug")
        retry_env.add_argument("--aws-account", required=True, help="AWS account name")

        # retry-app-deployment
        retry_deployment = subparsers.add_parser("retry-app-deployment", help="Retry a failed app deployment")
        retry_deployment.add_argument("--app", required=True, help="App slug")

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
            help="Owner username; required for Personal Assistant templates (app-type=personal-assistant + sidecar_enabled).",
        )
        deploy_tpl.add_argument(
            "--compute-mode",
            help="ECS compute mode override; defaults to template.default_compute_mode.",
        )
        deploy_tpl.add_argument(
            "--created-by",
            help="Username to attribute the deploy to (audit trail). Defaults to the first admin in the resolved org, then any superuser.",
        )

    def handle(self, *args, **options):
        operation = options.get("operation")

        if operation == "create-env":
            self._handle_create_env(options)
        elif operation == "teardown-env":
            self._handle_teardown_env(options)
        elif operation == "teardown-app":
            self._handle_teardown_app(options)
        elif operation == "retry-env-provisioning":
            self._handle_retry_env_provisioning(options)
        elif operation == "retry-app-deployment":
            self._handle_retry_app_deployment(options)
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

    def _handle_teardown_app(self, options):
        """Tear down an app's most recent deployment, and optionally remove the app entirely."""
        app_slug = options["app"]
        remove_app = options.get("remove_app", False)
        delete_secrets = options.get("delete_secrets", False)
        delete_efs_data = options.get("delete_efs_data", False)
        delete_policies = options.get("delete_policies", False)

        if not remove_app and (delete_secrets or delete_efs_data or delete_policies):
            self.stderr.write(self.style.ERROR(
                "--delete-secrets, --delete-efs-data, --delete-policies require --remove-app"
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
                delete_efs_data=delete_efs_data,
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
        deployment.status = models.Deployment.Status.TEARDOWN_PENDING
        deployment.status_message = "Teardown triggered via doh_control"
        deployment.save(update_fields=["status", "status_message", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nDeployment for '{app_slug}' set to TEARDOWN_PENDING"))
        self.stdout.write(f"  App: {app.name}")
        self.stdout.write(f"  Environment: {deployment.environment.name}")
        self.stdout.write(f"  Deployment: {deployment.id}")
        self.stdout.write(f"  Previous status: {old_status}")
        self.stdout.write(self.style.WARNING("Teardown will start automatically (job worker picks up pending teardowns)"))
        self.stdout.write("")

    def _queue_app_removal(self, app: models.App, delete_secrets: bool, delete_efs_data: bool, delete_policies: bool) -> None:
        """Queue an AppRemovalJob with teardown_first=True; the worker tears down live deployments inline, then removes the app."""
        if app.status == models.App.Status.PENDING_REMOVAL:
            self.stdout.write(self.style.WARNING(f"App '{app.slug}' is already pending removal"))
            return

        with transaction.atomic():
            job = models.AppRemovalJob.objects.create(
                organization=app.organization,
                app_id_snapshot=app.id,
                app_slug_snapshot=app.slug,
                app_name_snapshot=app.name,
                workspace_slug_snapshot=app.workspace.slug,
                delete_secrets=delete_secrets,
                delete_efs_data=delete_efs_data,
                delete_policies=delete_policies,
                teardown_first=True,
                created_by=None,
                status_message="Queued via doh_control teardown-app --remove-app",
            )
            app.status = models.App.Status.PENDING_REMOVAL
            app.save(update_fields=["status", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"\nApp '{app.slug}' set to PENDING_REMOVAL"))
        self.stdout.write(f"  App: {app.name}")
        self.stdout.write(f"  Workspace: {app.workspace.name}")
        self.stdout.write(f"  Removal job: {job.id}")
        self.stdout.write(f"  teardown_first: True")
        self.stdout.write(f"  delete_secrets: {delete_secrets}")
        self.stdout.write(f"  delete_efs_data: {delete_efs_data}")
        self.stdout.write(f"  delete_policies: {delete_policies}")
        self.stdout.write(self.style.WARNING(
            "Worker will tear down all live deployments inline, then perform cleanup + cascade delete"
        ))
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

        if deployment.status == models.Deployment.Status.SUCCEEDED:
            self.stderr.write(self.style.ERROR(f"Deployment already succeeded - nothing to retry"))
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
        """True iff the template is a Personal Assistant (sidecar_enabled + app-type=personal-assistant)."""
        if not template.sidecar_enabled:
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
