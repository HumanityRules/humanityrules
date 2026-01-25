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
    current_organization = models.ForeignKey(
        "Organization",
        on_delete=models.PROTECT,
        related_name="current_users",
        help_text="The organization the user is currently working in",
    )

    def __str__(self):
        return self.email or self.username


class Organization(models.Model):
    """
    Top-level tenant. Users belong to organizations, and organizations own workspaces.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
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

    def __str__(self):
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

    def __str__(self):
        return f"{self.name} ({self.aws_account_id or 'pending'})"

    def get_cloudformation_url(self):
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

    def __str__(self):
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

    def __str__(self):
        return self.full_name


class Environment(models.Model):
    """
    An environment within an AWS account (e.g., default, prod, staging).
    Environments are account-scoped: multiple workspaces can deploy to the same environment.
    Each environment has its own VPC and ECS cluster.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # Created, no infra yet
        PROVISIONING = "provisioning", "Provisioning"  # Base infra deploying
        READY = "ready", "Ready"  # VPC + cluster exist
        ERROR = "error", "Error"  # Provisioning failed

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

    def __str__(self):
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

    def __str__(self):
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

    def __str__(self):
        return f"{self.name} ({self.engine})"


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

    # Container configuration
    container_port = models.IntegerField()
    cpu = models.IntegerField(help_text="Fargate CPU units (256, 512, 1024, etc.)")
    memory = models.IntegerField(help_text="Fargate memory in MiB")
    health_check_path = models.CharField(max_length=255)
    health_check_command = models.CharField(
        max_length=500,
        blank=True,
        help_text="Health check command for non-HTTP health checks",
    )

    # Environment
    environment_variables = models.JSONField(
        default=list,
        help_text="List of {name, value} environment variable objects",
    )

    # Database binding
    datastore = models.ForeignKey(
        Datastore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="apps",
    )

    # App secrets for AWS Secrets Manager
    app_secrets = models.JSONField(
        null=True,
        blank=True,
        help_text="Dict mapping secret field names to values. Use null value to auto-generate.",
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

    def __str__(self):
        return self.name


class Conversation(models.Model):
    """A conversation between a user and the agent."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

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
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
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

    def __str__(self):
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

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class Deployment(models.Model):
    """A deployment of an app to infrastructure."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        BUILDING = "building", "Building Image"
        PUSHING = "pushing", "Pushing to ECR"
        DEPLOYING = "deploying", "Deploying Infrastructure"
        STARTING = "starting", "Starting Service"
        RUNNING = "running", "Running"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled Back"
        TEARDOWN_PENDING = "teardown_pending", "Teardown Pending"
        TEARING_DOWN = "tearing_down", "Tearing Down"
        TORN_DOWN = "torn_down", "Torn Down"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
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

    def __str__(self):
        return f"{self.app.name} - {self.git_ref} ({self.status})"


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

    def __str__(self):
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

    def __str__(self):
        return f"[{self.source}] {self.level}: {self.message[:50]}..."


# =============================================================================
# Signals
# =============================================================================

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=Organization)
def create_default_workspace(sender, instance, created, **kwargs):
    """Create a Default workspace when an Organization is created."""
    if created:
        Workspace.objects.create(
            organization=instance,
            name="Default",
            slug="default",
            description="Your starting workspace for apps and datastores. Rename or create additional workspaces to organize by team or project.",
        )
