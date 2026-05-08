import urllib.parse
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model extending Django's AbstractUser.
    WorkOS will handle authentication; workos_user_id links to the external identity.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    workos_user_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="The user ID from WorkOS",
    )
    oidc_sub = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="OIDC subject identifier",
    )
    current_organization = models.ForeignKey(
        "Organization",
        on_delete=models.PROTECT,
        related_name="current_users",
        help_text="The organization the user is currently working in",
    )

    def __str__(self) -> str:
        return self.email or self.username

    def save(self, *args, **kwargs) -> None:
        # Lock username against post-creation mutation. ABAC policies use
        # username as a stable identity anchor (see docs/policy_proxy_design.md
        # and the $resource.owner self-referential policy form); letting it
        # drift would invalidate those policies or enable takeover of
        # owned-by resources (e.g. Personal Assistants).
        if self.pk is not None:
            current = type(self).objects.filter(pk=self.pk).values_list("username", flat=True).first()
            if current is not None and current != self.username:
                raise ValueError(
                    f"User.username is immutable after creation "
                    f"(attempted {current!r} -> {self.username!r}).",
                )
        super().save(*args, **kwargs)


class Organization(models.Model):
    """
    Top-level tenant. Users belong to organizations, and organizations own workspaces.
    """

    class AuthProvider(models.TextChoices):
        WORKOS = "workos"
        OIDC = "oidc"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    default_org_role = models.CharField(
        max_length=100,
        default="viewer",
        help_text="Org-role automatically assigned to new identities joining this organization",
    )
    auth_provider = models.CharField(
        max_length=10,
        choices=AuthProvider.choices,
        default=AuthProvider.WORKOS,
    )
    oidc_issuer_url = models.CharField(max_length=500, blank=True)
    oidc_client_id = models.CharField(max_length=255, blank=True)
    oidc_client_secret = models.CharField(max_length=500, blank=True)
    bootstrap_admin_email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class OrganizationMembership(models.Model):
    """
    Links users to organizations with a specific role.
    """
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["user", "organization"]]
        verbose_name = "Organization Membership"
        verbose_name_plural = "Organization Memberships"

    def __str__(self) -> str:
        return f"{self.user} - {self.organization} ({self.role})"


class AWSAccount(models.Model):
    """
    Represents an AWS account connected to an organization.
    DevOpsHero uses CloudFormation to create an IAM role with AssumeRole access.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # Waiting for CloudFormation stack deployment
        CONNECTED = "connected", "Connected"  # Successfully verified
        ERROR = "error", "Error"  # Connection verification failed

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="aws_accounts",
    )
    name = models.CharField(
        max_length=255,
        help_text="User-friendly name for this AWS account (e.g., 'Production', 'Staging')",
    )
    aws_account_id = models.CharField(
        max_length=12,
        blank=True,
        help_text="The 12-digit AWS account ID (populated after verification)",
    )
    external_id = models.UUIDField(
        default=uuid.uuid4,
        help_text="External ID for secure cross-account AssumeRole",
    )
    role_arn = models.CharField(
        max_length=2048,
        blank=True,
        help_text="IAM role ARN that DevOpsHero assumes for deployments",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    status_message = models.TextField(
        blank=True,
        help_text="Additional status information or error details",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_aws_accounts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AWS Account"
        verbose_name_plural = "AWS Accounts"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_aws_account_name_per_org",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.aws_account_id or 'pending'})"

    def get_cloudformation_url(self) -> str:
        """Generate the AWS CloudFormation quick-create URL for this account."""
        params = {
            "stackName": f"DevOpsHero-{self.id.hex[:8]}",
            "templateURL": "https://devopshero-public.s3.us-east-1.amazonaws.com/cf_install_template.json",
            "param_ExternalId": str(self.external_id),
        }
        base_url = "https://us-east-1.console.aws.amazon.com/cloudformation/home"
        return f"{base_url}?region=us-east-1#/stacks/quickcreate?{urllib.parse.urlencode(params)}"


class GitProviderIntegration(models.Model):
    """
    Organization-level connection to a Git provider (GitHub, GitLab).
    Stores authentication credentials for accessing repositories.
    """

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="git_integrations",
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    installation_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="GitHub App installation ID",
    )
    access_token_encrypted = models.TextField(
        blank=True,
        help_text="Encrypted OAuth access token",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Git Provider Integration"
        verbose_name_plural = "Git Provider Integrations"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.organization.name} - {self.get_provider_display()}"


class Repository(models.Model):
    """
    A Git repository connected to an organization.
    Can be from GitHub, GitLab, or a local file:// URL.
    """

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"
        LOCAL = "local", "Local"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="repositories",
    )
    integration = models.ForeignKey(
        GitProviderIntegration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="repositories",
        help_text="Git provider integration (null for local repos)",
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Provider's repository ID",
    )
    name = models.CharField(
        max_length=255,
        help_text="Repository name (e.g., 'flask-api')",
    )
    full_name = models.CharField(
        max_length=500,
        help_text="Full repository name (e.g., 'acme/flask-api')",
    )
    default_branch = models.CharField(
        max_length=255,
        default="main",
    )
    clone_url = models.URLField(
        max_length=2048,
        help_text="HTTPS clone URL or file:// for local repos",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Repository"
        verbose_name_plural = "Repositories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "full_name"],
                name="unique_repo_full_name_per_org",
            )
        ]

    def __str__(self) -> str:
        return self.full_name


class Environment(models.Model):
    """
    An environment within an AWS account (e.g., default, prod, staging).
    Environments are account-scoped: multiple workspaces can deploy to the same environment.
    Each environment has its own VPC and ECS cluster.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"  # Editable setup draft, not yet queued
        PENDING = "pending", "Pending"  # Created, no infra yet
        PROVISIONING = "provisioning", "Provisioning"  # Base infra deploying
        READY = "ready", "Ready"  # VPC + cluster exist
        ERROR = "error", "Error"  # Provisioning failed
        DISCARDED = "discarded", "Discarded"  # Abandoned setup draft
        TEARDOWN_PENDING = "teardown_pending", "Teardown Pending"  # Queued for teardown
        TEARING_DOWN = "tearing_down", "Tearing Down"  # Teardown in progress

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    aws_account = models.ForeignKey(
        AWSAccount,
        on_delete=models.CASCADE,
        related_name="environments",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    aws_region = models.CharField(
        max_length=50,
        help_text="AWS region for this environment (e.g., us-east-1)",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    status_message = models.TextField(blank=True)

    # Stack names (set when provisioning starts)
    vpc_stack_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="CloudFormation stack name for VPC",
    )
    cluster_stack_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="CloudFormation stack name for ECS cluster",
    )

    # AWS outputs (populated after provisioning)
    vpc_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="VPC ID after provisioning",
    )
    cluster_arn = models.CharField(
        max_length=2048,
        blank=True,
        help_text="ECS cluster ARN after provisioning",
    )

    # Shared ALB configuration
    shared_alb_hosted_zone = models.CharField(
        max_length=255,
        blank=True,
        help_text="Hosted zone for shared ALB wildcard cert (e.g., 'dev.example.com'). Empty = HTTP only.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Environment"
        verbose_name_plural = "Environments"
        ordering = ["name"]
        unique_together = [["aws_account", "slug"]]

    def __str__(self) -> str:
        return f"{self.name} ({self.aws_account.name})"


class Workspace(models.Model):
    """A workspace for governance and policy. Contains apps and datastores."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="workspaces",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_workspaces",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["organization", "slug"]]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name


class Datastore(models.Model):
    """A managed database within a workspace."""

    class Engine(models.TextChoices):
        AURORA_MYSQL = "aurora-mysql", "Aurora MySQL"
        AURORA_POSTGRESQL = "aurora-postgresql", "Aurora PostgreSQL"

    class DeploymentMode(models.TextChoices):
        SERVERLESS_V2 = "aurora_serverless_v2", "Aurora Serverless v2"
        PROVISIONED = "aurora_provisioned", "Aurora Provisioned"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CREATING = "creating", "Creating"
        AVAILABLE = "available", "Available"
        ERROR = "error", "Error"
        DELETING = "deleting", "Deleting"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="datastores",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)

    # Engine configuration
    engine = models.CharField(
        max_length=50,
        choices=Engine.choices,
    )
    engine_version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Engine version (uses default if blank)",
    )

    # Deployment mode
    deployment_mode = models.CharField(
        max_length=50,
        choices=DeploymentMode.choices,
    )
    serverless_min_acu = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum ACUs for serverless mode",
    )
    serverless_max_acu = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum ACUs for serverless mode",
    )
    provisioned_instance_class = models.CharField(
        max_length=50,
        blank=True,
        help_text="Instance class for provisioned mode",
    )

    # Database
    database_name = models.CharField(max_length=255)

    # Security
    storage_encrypted = models.BooleanField(default=True)
    deletion_protection = models.BooleanField(default=False)
    backup_retention_days = models.IntegerField(default=7)

    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    status_message = models.TextField(blank=True)

    # AWS resources (populated after creation)
    cluster_arn = models.CharField(max_length=2048, blank=True)
    cluster_endpoint = models.CharField(max_length=255, blank=True)
    credentials_secret_arn = models.CharField(max_length=2048, blank=True)
    connection_secret_arn = models.CharField(max_length=2048, blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_datastores",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["workspace", "slug"]]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.engine})"


class EcsComputeMode(models.TextChoices):
    """Supported ECS compute backends for customer apps."""

    FARGATE = "fargate", "Fargate"
    EC2 = "ec2", "EC2 Capacity"


class AppTemplate(models.Model):
    """Pre-configured recipe for deploying a specific type of application."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField()
    icon = models.CharField(max_length=50, help_text="Emoji or icon class for template picker UI")
    category = models.CharField(max_length=50, help_text="e.g. ai-assistant, web-app, api")

    # Blueprint defaults (task-level)
    cpu = models.IntegerField()
    memory = models.IntegerField()
    default_compute_mode = models.CharField(
        max_length=20,
        choices=EcsComputeMode.choices,
        default=EcsComputeMode.FARGATE,
        help_text="Default ECS compute backend for deployments created from this template.",
    )

    # Ordered, non-empty list of container dicts. Each entry:
    #   {
    #     "name": "<stable identifier>",
    #     "image_source": "dockerfile" | "prebuilt" | "registry" | "policy_proxy",
    #     # image_source == "dockerfile":
    #     "source_repo_path": "hermes_agent",
    #     "dockerfile_path": "Dockerfile",
    #     # image_source == "prebuilt":
    #     "ecr_repo": "sidecar-mcp",    # within doh/{env_slug}/ namespace
    #     "version": "0.1.0",
    #     # image_source == "registry":
    #     "registry_image": "docker:26.1.0-dind",
    #     # image_source == "policy_proxy":
    #     "upstream_container": "<sibling container name>",
    #     # common:
    #     "container_port": 8787,
    #     "health_check_path": "/health",
    #     "health_check_command": "",
    #     "health_check_grace_period": 0,
    #     "efs_mounts": ["home", "workspace"],  # names from efs_config.mounts
    #     "environment": {NAME: value, ...},     # DOH-managed platform constants
    #     "configurable_variables": [ ... {name, category, value, user_editable, ...} ... ],
    #   }
    containers = models.JSONField(default=list)

    # Name of the container in `containers` that receives ALB traffic.
    # Null = no ALB exposure (task-internal only).
    alb_target_container = models.CharField(max_length=64, null=True, blank=True)

    # Datastore requirements (null = no datastore needed)
    datastore_config = models.JSONField(null=True, blank=True)

    # EFS configuration. Shape:
    #   {"mounts": [{"name": str, "subpath": str, "container_path": str,
    #                "posix_uid": int, "posix_gid": int}, ...]}
    # Each mount becomes a per-app AccessPoint on the shared EFS filesystem,
    # rooted at /deployments/<app_slug>/<subpath>. Containers opt in by name
    # via container["efs_mounts"]. null = no EFS.
    efs_config = models.JSONField(null=True, blank=True)

    # Default resource tags stamped on every app deployed from this template.
    # List of {key, value}. Used by the deploy flow to write ResourceTag rows
    # at app creation time — e.g. [{"key": "app-type", "value": "personal-assistant"}].
    default_tags = models.JSONField(default=list, blank=True)

    # Platform-owned infrastructure capabilities needed by apps from this
    # template. CDK interprets these into task-role grants and other platform
    # wiring; they are not user-managed app permissions.
    platform_capabilities = models.JSONField(default=list, blank=True)

    # Template for the default App Name shown on the deploy form. Tokens:
    #   {username} - owner's username; email local-part with non-alnum stripped
    #   {index}    - zero-padded (2-digit) counter that picks the lowest free slug in the org
    # Empty string = fall back to template.name.
    prefill_name = models.CharField(max_length=200, blank=True, default="")

    is_active = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class App(models.Model):
    """An application within a workspace."""

    class AppType(models.TextChoices):
        WEB = "web", "Web Service"
        WORKER = "worker", "Background Worker"
        SCHEDULED = "scheduled", "Scheduled Job"

    class BuildStrategy(models.TextChoices):
        DOCKERFILE = "dockerfile", "Dockerfile"
        NIXPACKS = "nixpacks", "Nixpacks (auto-detect)"
        BUILDPACK = "buildpack", "Cloud Native Buildpack"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PENDING_REMOVAL = "pending_removal", "Pending Removal"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        help_text="Denormalized from workspace for unique constraint on (organization, slug)",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="apps",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.PROTECT,
        related_name="apps",
    )
    source_template = models.ForeignKey(
        AppTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deployed_apps",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    app_type = models.CharField(
        max_length=20,
        choices=AppType.choices,
    )
    build_strategy = models.CharField(
        max_length=20,
        choices=BuildStrategy.choices,
    )

    # Source configuration
    repo_subpath = models.CharField(
        max_length=500,
        blank=True,
        help_text="Subdirectory within repository (for monorepos, optional)",
    )
    branch = models.CharField(max_length=255)
    dockerfile_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Path to Dockerfile if using dockerfile build strategy",
    )

    # Container configuration (identity/build — runtime fields moved to DeploymentBlueprint)
    container_port = models.IntegerField()
    health_check_path = models.CharField(max_length=255)
    health_check_command = models.CharField(
        max_length=500,
        blank=True,
        help_text="Health check command for non-HTTP health checks",
    )
    health_check_grace_period = models.IntegerField(default=0, help_text="ECS health check grace period in seconds. 0 = use environment default.")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_apps",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                name="unique_app_slug_per_org",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Conversation(models.Model):
    """A conversation between a user and the agent."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    class Mode(models.TextChoices):
        GENERAL = "general", "General"
        ENVIRONMENT_SETUP = "environment_setup", "Environment Setup"
        APP_DEPLOYMENT = "app_deployment", "App Deployment"
        PERMISSIONS = "permissions", "Permissions"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    context_repository = models.ForeignKey(
        Repository,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="Repository context for this conversation (set via UI)",
    )
    context_workspace = models.ForeignKey(
        Workspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="Workspace context for this conversation (set via UI)",
    )
    context_aws_account = models.ForeignKey(
        AWSAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="AWS account context for this conversation (set via UI for environment creation)",
    )
    context_environment = models.ForeignKey(
        "Environment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="Environment context for environment-setup conversations",
    )
    context_app = models.ForeignKey(
        "App",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="App context for deployment-mode conversations",
    )
    context_deployment_blueprint = models.ForeignKey(
        "DeploymentBlueprint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="DeploymentBlueprint context for deployment-mode conversations",
    )
    context_app_permission_request = models.ForeignKey(
        "AppPermissionRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="AppPermissionRequest context for permissions-mode conversations",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.GENERAL,
        help_text="Conversation mode determines system prompt and model selection",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Conversation title (placeholder for now)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    session_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Claude Agent SDK session ID for conversation continuity",
    )
    deployments = models.ManyToManyField(
        "Deployment",
        blank=True,
        related_name="conversations",
        help_text="Deployments triggered from this conversation",
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Conversation {self.id}"


class Message(models.Model):
    """A single message in a conversation."""

    class Role(models.TextChoices):
        USER = "user", "User"
        AGENT = "agent", "Agent"
        SYSTEM = "system", "System"

    class ContentType(models.TextChoices):
        TEXT = "text", "Text"
        MARKDOWN = "markdown", "Markdown"
        CODE = "code", "Code Block"
        PROGRESS = "progress", "Progress Indicator"
        CHOICE = "choice", "Interactive Choice"
        DEPLOYMENT_LOG = "deployment_log", "Deployment Log"
        ERROR = "error", "Error"
        TOOL_CALL = "tool_call", "Tool Call"
        SYSTEM_TRIGGER = "system_trigger", "System Trigger"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
    )
    content_type = models.CharField(
        max_length=20,
        choices=ContentType.choices,
    )
    content = models.TextField(
        help_text="Message content (JSON for structured types, plain text for text/markdown)",
    )
    metadata = models.JSONField(
        default=dict,
        help_text="Extra data: code language, choice options, log level, etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:50]}..."


class DeploymentBlueprint(models.Model):
    """Desired deployable state for one (app, environment) pair."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DEPLOYING = "deploying", "Deploying"
        FAILED = "failed", "Failed"
        ACTIVE = "active", "Active"
        DISCARDED = "discarded", "Discarded"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    app = models.ForeignKey(
        App,
        on_delete=models.CASCADE,
        related_name="blueprints",
    )
    environment = models.ForeignKey(
        Environment,
        on_delete=models.PROTECT,
        related_name="blueprints",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    status_message = models.TextField(blank=True)

    branch = models.CharField(
        max_length=255,
        blank=True,
        help_text="Branch override for this environment. Blank = use Repository.default_branch.",
    )
    cpu = models.IntegerField(help_text="ECS task CPU units (256, 512, 1024, etc.)")
    memory = models.IntegerField(help_text="ECS task memory in MiB")
    compute_mode = models.CharField(
        max_length=20,
        choices=EcsComputeMode.choices,
        default=EcsComputeMode.FARGATE,
        help_text="ECS compute backend for this app in this environment.",
    )

    # Per-container materialized runtime values. Mirrors the template's
    # `containers` shape (one entry per container, ordered), each carrying
    # its own `environment_variables` (list of {name, value}) and
    # `app_secrets` (dict of field -> value|""|None). Same schema that
    # _materialize_environment_variables / _materialize_app_secrets produce
    # from a container's configurable_variables.
    containers = models.JSONField(default=list)

    datastore = models.ForeignKey(
        Datastore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blueprints",
    )

    subdomain = models.CharField(
        max_length=63,
        blank=True,
        help_text="Route53 subdomain. Defaults to app slug, auto-suffixed with -env if conflict.",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_blueprints",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Deployment Blueprint"
        verbose_name_plural = "Deployment Blueprints"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.app.name} -> {self.environment.name} ({self.status})"


class Deployment(models.Model):
    """An execution record for one attempt to apply a blueprint."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        BUILDING = "building", "Building Image"
        PUSHING = "pushing", "Pushing to ECR"
        DEPLOYING = "deploying", "Deploying"
        STARTING = "starting", "Starting"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled Back"
        TORN_DOWN = "torn_down", "Torn Down"
        TEARDOWN_PENDING = "teardown_pending", "Teardown Pending"
        TEARING_DOWN = "tearing_down", "Tearing Down"

    IN_PROGRESS_STATUSES = (
        Status.PENDING,
        Status.BUILDING,
        Status.PUSHING,
        Status.DEPLOYING,
        Status.STARTING,
    )

    CONCLUDED_STATUSES = (
        Status.SUCCEEDED,
        Status.FAILED,
        Status.TEARDOWN_PENDING,
        Status.TEARING_DOWN,
        Status.TORN_DOWN,
    )

    VISIBLE_STATUSES = (
        *IN_PROGRESS_STATUSES,
        *CONCLUDED_STATUSES,
    )

    TRANSIENT_STATUSES = (
        *IN_PROGRESS_STATUSES,
        Status.TEARDOWN_PENDING,
        Status.TEARING_DOWN,
    )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    blueprint = models.ForeignKey(
        DeploymentBlueprint,
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    app = models.ForeignKey(
        App,
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    environment = models.ForeignKey(
        Environment,
        on_delete=models.PROTECT,
        related_name="deployments",
        help_text="The environment this deployment targets",
    )

    # Source
    git_ref = models.CharField(
        max_length=255,
        help_text="Branch, tag, or commit SHA",
    )
    git_commit_sha = models.CharField(
        max_length=40,
        blank=True,
        help_text="Resolved commit SHA",
    )
    git_commit_message = models.CharField(
        max_length=500,
        blank=True,
    )

    # Image
    image_tag = models.CharField(max_length=255)
    image_uri = models.CharField(
        max_length=2048,
        blank=True,
        help_text="Full ECR image URI (set after push)",
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    status_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Routing
    subdomain = models.CharField(
        max_length=63,
        blank=True,
        help_text="Route53 subdomain. Defaults to app slug, auto-suffixed with -env if conflict.",
    )

    # Outputs
    service_url = models.URLField(
        max_length=2048,
        blank=True,
        help_text="URL where the deployed service is accessible",
    )
    alb_dns = models.CharField(
        max_length=255,
        blank=True,
        help_text="ALB DNS name",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_deployments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.app.name} - {self.git_ref} ({self.status})"

    @property
    def is_in_progress(self) -> bool:
        """Actively going through the build/deploy pipeline."""
        return self.status in self.IN_PROGRESS_STATUSES

    @property
    def is_concluded(self) -> bool:
        """Reached a result from the pipeline's perspective."""
        return self.status in self.CONCLUDED_STATUSES

    @property
    def is_transient(self) -> bool:
        """Status may change via background processing."""
        return self.status in self.TRANSIENT_STATUSES


class DeploymentLog(models.Model):
    """Log entries from a deployment."""

    class Level(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        ERROR = "error", "Error"

    class Source(models.TextChoices):
        APP = "app", "App"
        CDK = "cdk", "CDK"
        DOCKER = "docker", "Docker"
        SYSTEM = "system", "System"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.APP,
    )
    level = models.CharField(
        max_length=20,
        choices=Level.choices,
    )
    message = models.TextField()
    details = models.JSONField(
        null=True,
        blank=True,
        help_text="Structured data: stack name, resource ARN, etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Deployment Log"
        verbose_name_plural = "Deployment Logs"

    def __str__(self) -> str:
        return f"[{self.source}] {self.level}: {self.message[:50]}..."


class EnvironmentLog(models.Model):
    """Log entries from environment provisioning."""

    class Level(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        ERROR = "error", "Error"

    class Source(models.TextChoices):
        SYSTEM = "system", "System"
        CDK = "cdk", "CDK"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.SYSTEM,
    )
    level = models.CharField(
        max_length=20,
        choices=Level.choices,
    )
    message = models.TextField()
    details = models.JSONField(
        null=True,
        blank=True,
        help_text="Structured data: stack name, resource ARN, etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["environment", "created_at"])]
        verbose_name = "Environment Log"
        verbose_name_plural = "Environment Logs"

    def __str__(self) -> str:
        return f"[{self.source}] {self.level}: {self.message[:50]}..."


class WaitlistSignup(models.Model):
    """Captures email signups from the landing page 'notify me' form."""
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    email = models.EmailField(unique=True)
    source = models.CharField(
        max_length=50,
        blank=True,
        help_text="Where on the landing page they signed up (hero, cta)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Waitlist Signup"
        verbose_name_plural = "Waitlist Signups"

    def __str__(self) -> str:
        return self.email


class LLMUsageLog(models.Model):
    """Append-only log of every LLM interaction for cost tracking and billing."""

    class Source(models.TextChoices):
        AGENT_TURN = "agent_turn", "Agent Turn"
        TITLE_GENERATION = "title_generation", "Title Generation"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="llm_usage_logs",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="llm_usage_logs",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        related_name="llm_usage_logs",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
    )
    model_alias = models.CharField(
        max_length=50,
        help_text="Model alias used (e.g., 'opus-4.6', 'haiku-4.5')",
    )
    model_id = models.CharField(
        max_length=255,
        help_text="Resolved API or Bedrock model ID",
    )
    input_tokens = models.IntegerField(
        null=True,
        help_text="Number of input tokens consumed",
    )
    output_tokens = models.IntegerField(
        null=True,
        help_text="Number of output tokens generated",
    )
    cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        help_text="Cost in USD (from SDK or computed)",
    )
    duration_ms = models.IntegerField(
        null=True,
        help_text="Wall-clock duration in milliseconds",
    )
    num_turns = models.IntegerField(
        null=True,
        help_text="Number of agent turns (only for agent_turn source)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "LLM Usage Log"
        verbose_name_plural = "LLM Usage Logs"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.source} — {self.model_alias} — ${self.cost_usd or 0:.4f}"


class AppPermissions(models.Model):
    """The current (last-applied) permissions for an app in an environment."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="app_permissions")
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="app_permissions")
    statements = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("app", "environment")]

    def __str__(self) -> str:
        return f"AppPermissions {self.app.slug}/{self.environment.slug}"


class AppPermissionRequest(models.Model):
    """A request to modify IAM task-role policies for a deployed app."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED_PENDING_APPLY = "approved_pending_apply", "Approved - Pending Apply"
        APPLYING = "applying", "Applying"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="app_permission_requests")
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="app_permission_requests")
    statements = models.JSONField(default=list, help_text="List of policy statement dicts")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True, help_text="Human/agent-authored rationale for the permission changes")
    status_message = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_app_permission_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AppPermissionRequest {self.id} ({self.status})"


class AppRemovalJob(models.Model):
    """Async job to remove an App: optional EFS/secrets cleanup, then DB cascade delete."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "Organization", on_delete=models.CASCADE, related_name="app_removal_jobs",
    )
    # Snapshot fields so the job row remains meaningful after the App row is deleted
    app_id_snapshot = models.UUIDField()
    app_slug_snapshot = models.SlugField(max_length=255)
    app_name_snapshot = models.CharField(max_length=255)
    workspace_slug_snapshot = models.SlugField(max_length=255)
    delete_secrets = models.BooleanField(default=False)
    delete_efs_data = models.BooleanField(default=False)
    delete_policies = models.BooleanField(default=False)
    # If True, the executor first tears down every live deployment of this app
    # (calling app_deployment_teardown_executor.run_teardown inline) before
    # running the cleanup + DB cascade delete. Set by the CLI's
    # `doh_control teardown-app --remove-app` flow; the UI's "Remove App"
    # button leaves this False because it only enables when the app is already
    # not live.
    teardown_first = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_message = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AppRemovalJob {self.app_slug_snapshot} ({self.status})"


class AwsResourceCache(models.Model):
    """Cached AWS resource listings per environment and service."""
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="resource_caches")
    service = models.CharField(max_length=100)
    resources = models.JSONField(default=list, help_text="List of {arn, label} dicts")
    fetched_at = models.DateTimeField()

    class Meta:
        unique_together = [("environment", "service")]

    def __str__(self) -> str:
        return f"{self.environment} / {self.service} ({len(self.resources)} resources)"


# =============================================================================
# ABAC Models
# =============================================================================


class IdentityAttribute(models.Model):
    """Direct attribute on a user, scoped to an organization."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="identity_attributes",
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="identity_attributes",
    )
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("organization", "user", "key", "value")]

    def __str__(self) -> str:
        return f"{self.user} — {self.key}={self.value}"


class Group(models.Model):
    """Attribute container, org-scoped. Members inherit group attributes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="groups",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "name")]

    def __str__(self) -> str:
        return self.name


class GroupMembership(models.Model):
    """User-to-group link."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="memberships",
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="group_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("group", "user")]

    def __str__(self) -> str:
        return f"{self.user} in {self.group}"


class GroupAttribute(models.Model):
    """Key-value on a group, inherited by all members."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="attributes",
    )
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("group", "key", "value")]

    def __str__(self) -> str:
        return f"{self.group} — {self.key}={self.value}"


class ResourceTag(models.Model):
    """Tag on a resource (workspace, environment, or app). Exactly one FK must be set."""

    class ResourceType(models.TextChoices):
        WORKSPACE = "workspace", "Workspace"
        ENVIRONMENT = "environment", "Environment"
        APP = "app", "App"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="resource_tags",
    )
    resource_type = models.CharField(max_length=20, choices=ResourceType.choices)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="tags",
    )
    environment = models.ForeignKey(
        Environment, on_delete=models.CASCADE, null=True, blank=True, related_name="tags",
    )
    app = models.ForeignKey(
        App, on_delete=models.CASCADE, null=True, blank=True, related_name="tags",
    )
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(resource_type="workspace", workspace__isnull=False, environment__isnull=True, app__isnull=True)
                    | models.Q(resource_type="environment", workspace__isnull=True, environment__isnull=False, app__isnull=True)
                    | models.Q(resource_type="app", workspace__isnull=True, environment__isnull=True, app__isnull=False)
                ),
                name="resource_tag_exactly_one_fk",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.key}={self.value}"


class Policy(models.Model):
    """ABAC policy with JSON conditions mapping identity attributes + resource tags to actions."""

    class ResourceType(models.TextChoices):
        WORKSPACE = "workspace", "Workspace"
        ENVIRONMENT = "environment", "Environment"
        APP = "app", "App"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="policies",
    )
    name = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=20, choices=ResourceType.choices)
    identity_conditions = models.JSONField(
        default=list,
        help_text='List of {"key": "...", "value": "..."} dicts, AND-ed. [{"key": "*", "value": "*"}] for wildcard.',
    )
    resource_conditions = models.JSONField(
        default=list,
        help_text='List of {"key": "...", "value": "..."} dicts, AND-ed. [{"key": "*", "value": "*"}] for wildcard.',
    )
    actions = models.JSONField(
        default=list,
        help_text='List of action strings. Prefix with "!" for deny.',
    )
    is_system = models.BooleanField(default=False, help_text="Display-only flag for seed policies")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "name")]
        verbose_name_plural = "Policies"

    def clean(self) -> None:
        from devopshero_app.services import abac
        abac.validate_policy_conditions(
            identity_conditions=self.identity_conditions or [],
            resource_conditions=self.resource_conditions or [],
        )

    def __str__(self) -> str:
        return self.name


class EnvironmentBearerToken(models.Model):
    """
    Per-environment bearer token used by components running inside a customer
    env (policy proxies, Hermes, future env-resident services) to authenticate
    calls to DOH's control plane. One active token per environment; the raw
    token lives in the customer's AWS Secrets Manager
    (devopshero/{env-slug}/shared-secrets, key DOH_ENV_BEARER). Only the hash
    is stored here so DOH can authenticate incoming control-plane calls
    without ever seeing the raw value after issue.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    environment = models.OneToOneField(
        Environment,
        on_delete=models.CASCADE,
        related_name="env_bearer_token",
    )
    token_hash = models.CharField(
        max_length=128,
        unique=True,
        help_text="SHA-256 hex digest of the raw bearer token.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"EnvironmentBearerToken({self.environment.slug})"


class IntegrationConfig(models.Model):
    """DOH-global config for a third-party integration provider.

    One row per provider. `config` holds the provider-specific payload as-is —
    e.g. for Google, the contents of the `web` object from the OAuth client
    JSON downloaded from Google Cloud Console.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    provider = models.CharField(max_length=50, choices=Provider.choices, unique=True)
    config = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"IntegrationConfig({self.provider})"


# =============================================================================
# Signals
# =============================================================================

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=Organization)
def create_default_workspace(
    sender: type[Organization],
    instance: Organization,
    created: bool,
    **kwargs: object,
) -> None:
    """Create a Default workspace when an Organization is created."""
    if created:
        Workspace.objects.create(
            organization=instance,
            name="Default",
            slug="default",
            description="Your starting workspace for apps and datastores. Rename or create additional workspaces to organize by team or project.",
        )


@receiver(post_save, sender=Workspace)
def create_default_workspace_tag(
    sender: type[Workspace],
    instance: Workspace,
    created: bool,
    **kwargs: object,
) -> None:
    """Create a workspace-name tag when a new Workspace is created."""
    if created:
        from devopshero_app.services import abac
        abac.create_default_workspace_tag(instance)


@receiver(post_save, sender=Environment)
def create_default_environment_tag(
    sender: type[Environment],
    instance: Environment,
    created: bool,
    **kwargs: object,
) -> None:
    """Create an environment-name tag when a new Environment is created."""
    if created:
        from devopshero_app.services import abac
        abac.create_default_environment_tag(instance)


@receiver(post_save, sender=App)
def create_default_app_policy(
    sender: type[App],
    instance: App,
    created: bool,
    **kwargs: object,
) -> None:
    """Create a default app:use policy and app-name tag when a new App is created."""
    if created:
        from devopshero_app.services.abac import create_default_app_policy as _create_policy
        _create_policy(instance)
