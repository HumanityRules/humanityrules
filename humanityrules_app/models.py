import urllib.parse
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

import humanityrules_app.app_slugs as app_slugs


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

    class LlmPreset(models.TextChoices):
        BEDROCK = "bedrock", "Bedrock"
        CODEX = "codex", "OpenAI Codex"

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
    llm_preset = models.CharField(
        max_length=32,
        default=LlmPreset.CODEX,
        choices=LlmPreset.choices,
        help_text=(
            "LLM preset for personal assistants deployed by this org. Defaults to "
            "Codex; set to Bedrock per-customer in admin for the demo."
        ),
    )
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


class OrganizationInvite(models.Model):
    """
    Email-based invitation to join an Organization on the HUMR control plane.

    Issued by an org admin, consumed by the invitee at /invite/<token>/. Once
    accepted, the row is preserved (with `accepted_at` set) for audit. Email
    match against `request.user.email` is the security check at consumption
    time — the invite link itself is bearer-authoritative.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    email = models.EmailField(
        help_text="Address the invitation was sent to; the invitee must sign in with this email.",
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_sent",
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Secret bearer token embedded in the invite URL.",
    )
    role = models.CharField(
        max_length=20,
        choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Organization Invite"
        verbose_name_plural = "Organization Invites"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email} -> {self.organization} ({self.role})"


class AWSAccount(models.Model):
    """
    Represents an AWS account connected to an organization.
    HumanityRules uses CloudFormation to create an IAM role with AssumeRole access.
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
        help_text="IAM role ARN that Humanity Rules assumes for deployments",
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
    is_humr_sandbox = models.BooleanField(
        default=False,
        help_text=(
            "This row points at HumR's own shared sandbox account. Environments here "
            "reuse shared base infra instead of provisioning their own."
        ),
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

    # The install stack always lives in us-east-1 (see get_cloudformation_url).
    INSTALL_STACK_REGION = "us-east-1"

    def get_install_stack_name(self) -> str:
        """CloudFormation stack name HumanityRules onboards this account with.

        Derived from external_id (random uuid4), not id (time-ordered uuid7): uuid7's first
        8 hex chars are the high bits of a ms timestamp, so they only change every ~65s and
        two accounts onboarded into the same AWS account within a minute would collide on the
        stack name. external_id's first 8 hex are random, so the name is effectively unique.
        """
        return f"HumanityRules-{self.external_id.hex[:8]}"

    def get_cloudformation_url(self, api_endpoint: str) -> str:
        """Generate the AWS CloudFormation quick-create URL for this account.

        api_endpoint is woven in as param_ApiEndpoint so the install Lambda reports back to
        whichever control plane issued this link (callers pass the origin the admin reached us
        on). Pass "" to let the Lambda fall back to its default (prod).
        """
        params = {
            "stackName": self.get_install_stack_name(),
            "templateURL": "https://humr-public.s3.us-east-1.amazonaws.com/cf_install_template.json",
            "param_ExternalId": str(self.external_id),
            "param_ApiEndpoint": api_endpoint,
        }
        base_url = "https://us-east-1.console.aws.amazon.com/cloudformation/home"
        return f"{base_url}?region=us-east-1#/stacks/quickcreate?{urllib.parse.urlencode(params)}"


class IntegrationGitProvider(models.Model):
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
        verbose_name = "Integration Git Provider"
        verbose_name_plural = "Integration Git Providers"
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
        IntegrationGitProvider,
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

    IN_PROGRESS_STATUSES = (
        Status.PENDING,
        Status.PROVISIONING,
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

    # Set at environment creation, immutable after: flipping it on a live
    # environment would require cycling container instances to a new type and
    # redeploying every app. True asserts the account+region has ECS
    # awsvpcTrunking enabled; instance type and task sizing derive from it in
    # services/infra_customer/node_packing.py. Not exposed in any UI yet.
    eni_trunking_enabled = models.BooleanField(default=False)

    claimed_by_run = models.ForeignKey(
        "humanityrules_app.JobWorkerRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Worker run that claimed this job; liveness input for stale-job detection",
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

    @property
    def is_transient(self) -> bool:
        """Status may change via background processing (provisioning or teardown)."""
        return self.status in self.TRANSIENT_STATUSES

    @classmethod
    def zone_claimants(cls, aws_account: AWSAccount) -> "models.QuerySet[Environment]":
        """All environments in the account that hold a hosted zone (an account can have several, one zone each).

        An environment owns its zone exclusively — its base stack creates the zone-wide
        `*.<zone>` wildcard record. Every path that offers or accepts a zone checks this
        queryset to keep a zone with one owner; a discarded draft holds no claim.
        """
        return cls.objects.filter(aws_account=aws_account).exclude(shared_alb_hosted_zone="").exclude(status=cls.Status.DISCARDED)


class Workspace(models.Model):
    """A workspace for governance and policy. Contains apps."""

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

    # Runtime defaults (task-level)
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
    #     "ecr_repo": "sidecar-mcp",    # within humr/{env_slug}/ namespace
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
    #     "host_mounts": [{"source_path": "/var/lib/...", "container_path": "/mnt"}],
    #     "linux_capabilities": ["SYS_ADMIN"],
    #     "environment": {NAME: value, ...},     # HUMR-managed platform constants
    #     "configurable_variables": [ ... {name, category, value, user_editable, ...} ... ],
    #   }
    containers = models.JSONField(default=list)

    # Name of the container in `containers` that receives ALB traffic.
    # Null = no ALB exposure (task-internal only).
    alb_target_container = models.CharField(max_length=64, null=True, blank=True)

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

    # When True, the ECS service runs at max_healthy_percent=100, which forces
    # ECS to fully stop the old task before starting the replacement. Set for
    # templates whose stop handler writes durable state that the replacement
    # task must restore from (e.g., Hermes's EFS checkpoint tar). Default False:
    # stop and start can overlap, trading a checkpoint race for zero-downtime.
    serialize_task_replacement = models.BooleanField(default=False)

    # When True, the ALB listener rule also matches *-<agent-host> so the
    # agent's Caddy sidecar can route user webapps by hostname. TLS and DNS
    # come from the environment's *.<zone> certificate and wildcard record.
    # See docs/webapps_design.md.
    enable_webapp_hosts = models.BooleanField(default=False)

    # Template for the dashless default App Name shown on the deploy form. Tokens:
    #   {username} - owner's username; email local-part with non-alnum stripped
    #   {index}    - zero-padded (2-digit) counter that picks the lowest free slug in the org
    # Pattern output is normalized to lowercase letters and digits. Empty string falls back
    # to the template slug normalized the same way.
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
    environment = models.ForeignKey(
        Environment,
        on_delete=models.PROTECT,
        related_name="apps",
        help_text="The environment this app deploys to. Set at creation, immutable; the app is deleted when its environment is torn down.",
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
    slug = models.SlugField(
        max_length=255,
        validators=[app_slugs.validate_app_hostname_label],
        help_text="Hostname label containing lowercase letters and digits only.",
    )
    build_strategy = models.CharField(
        max_length=20,
        choices=BuildStrategy.choices,
    )

    # Source configuration. The build branch is Repository.default_branch.
    repo_subpath = models.CharField(
        max_length=500,
        blank=True,
        help_text="Subdirectory within repository (for monorepos, optional)",
    )
    dockerfile_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="Path to Dockerfile if using dockerfile build strategy",
    )

    # Container configuration
    container_port = models.IntegerField()
    health_check_path = models.CharField(max_length=255)
    health_check_command = models.CharField(
        max_length=500,
        blank=True,
        help_text="Health check command for non-HTTP health checks",
    )
    health_check_grace_period = models.IntegerField(default=0, help_text="ECS health check grace period in seconds. 0 = use environment default.")

    # Runtime configuration
    cpu = models.IntegerField(help_text="ECS task CPU units (256, 512, 1024, etc.)")
    memory = models.IntegerField(help_text="ECS task memory in MiB")
    compute_mode = models.CharField(
        max_length=20,
        choices=EcsComputeMode.choices,
        default=EcsComputeMode.FARGATE,
        help_text="ECS compute backend for this app.",
    )

    # Per-container materialized runtime values. Mirrors the template's
    # `containers` shape (one entry per container, ordered), each carrying
    # its own `environment_variables` (list of {name, value}) and
    # `app_secrets` (dict of field -> value|""|None). Same schema that
    # _materialize_environment_variables / _materialize_app_secrets produce
    # from a container's configurable_variables.
    containers = models.JSONField(default=list)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    # Free-form tag used to scope async work to a specific job worker. The unscoped
    # main worker (no --label) only claims rows where label="" — the default for
    # every App created via the UI. A worktree-local worker started with
    # `run_job_worker --label foo` only claims rows whose App has label="foo",
    # which avoids cross-worktree collisions during end-to-end verification.
    label = models.CharField(max_length=64, blank=True, default="")

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


class SandboxSlugClaim(models.Model):
    """Globally-unique, first-come reservation of an app slug in the shared HumR sandbox.

    Every org's sandbox resolves to one shared base infra, so app resources are named
    humr-sandbox-{slug}-* across all orgs — the slug is a single global namespace. The
    UNIQUE(slug) here is the atomic backstop: two concurrent first-time deploys racing the
    same new slug can't both win the INSERT (a plain check-then-create lets both pass).
    Only the shared sandbox writes here; dedicated customer accounts have a private AWS
    account per org and may legitimately reuse a slug across orgs. There is no FK to App:
    the claim is reserved before the App row exists (so a race loss creates no orphan App)
    and released explicitly on app removal (see app_remove_executor / sandbox_service).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    slug = models.SlugField(max_length=255, unique=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="sandbox_slug_claims",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.slug


class Deployment(models.Model):
    """An execution record for one attempt to deploy an app."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DEPLOYING = "deploying", "Deploying"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        TORN_DOWN = "torn_down", "Torn Down"
        TEARDOWN_PENDING = "teardown_pending", "Teardown Pending"
        TEARING_DOWN = "tearing_down", "Tearing Down"

    SETTLED_STATUSES = (
        Status.SUCCEEDED,
        Status.FAILED,
        Status.TORN_DOWN,
    )

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

    claimed_by_run = models.ForeignKey(
        "humanityrules_app.JobWorkerRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Worker run that claimed this job; liveness input for stale-job detection",
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
    def is_settled(self) -> bool:
        """Return whether no deployment or teardown work is queued or running."""
        return self.status in self.SETTLED_STATUSES


class AppEnvironmentActivity(models.Model):
    """Latest runtime activity observed for one app in its environment."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid7,
        editable=False,
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="app_environment_activities",
    )
    app = models.OneToOneField(
        App,
        on_delete=models.CASCADE,
        related_name="environment_activity",
    )
    last_policy_proxy_activity_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App Environment Activity"
        verbose_name_plural = "App Environment Activities"
        indexes = [
            models.Index(fields=["organization", "last_policy_proxy_activity_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.app.slug}: {self.last_policy_proxy_activity_at}"


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


class AppPermissions(models.Model):
    """The current (last-applied) permissions for an app."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    app = models.OneToOneField(App, on_delete=models.CASCADE, related_name="app_permissions")
    statements = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"AppPermissions {self.app.slug}"


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
    statements = models.JSONField(default=list, help_text="List of policy statement dicts")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True, help_text="Human/agent-authored rationale for the permission changes")
    status_message = models.TextField(blank=True)
    claimed_by_run = models.ForeignKey(
        "humanityrules_app.JobWorkerRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Worker run that claimed this job; liveness input for stale-job detection",
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="created_app_permission_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["app"],
                condition=models.Q(status="applying"),
                name="unique_applying_permission_request_per_app",
            ),
        ]

    def __str__(self) -> str:
        return f"AppPermissionRequest {self.id} ({self.status})"


class AppRemovalJob(models.Model):
    """Async job to remove an App: optional persistent-data/secrets/policy cleanup, then DB cascade delete.

    `delete_persistent_data` covers both EFS app data (`/deployments/{app_slug}` on the
    env's shared EFS, when the template declares `efs_config`) and EC2 host bind-mount
    data (paths from `template.containers[*].host_mounts[*].source_path`, when any
    container declares `host_mounts`). The executor skips each branch when the template
    has nothing of that kind to clean.
    """

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
    delete_persistent_data = models.BooleanField(default=False)
    delete_policies = models.BooleanField(default=False)
    # If True, the executor first tears down every live deployment of this app
    # (calling app_deployment_teardown_executor.run_teardown inline) before
    # running the cleanup + DB cascade delete. Set by the CLI's
    # `humr_control teardown-app --remove-app` flow; the UI's "Remove App"
    # button leaves this False because it only enables when the app is already
    # not live.
    teardown_first = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_message = models.TextField(blank=True)
    claimed_by_run = models.ForeignKey(
        "humanityrules_app.JobWorkerRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Worker run that claimed this job; liveness input for stale-job detection",
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["app_id_snapshot"],
                condition=models.Q(status__in=("pending", "running")),
                name="unique_active_app_removal_per_app",
            ),
        ]

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
    """Tag on a resource (workspace, environment, app, or credential). Exactly one FK must be set."""

    class ResourceType(models.TextChoices):
        WORKSPACE = "workspace", "Workspace"
        ENVIRONMENT = "environment", "Environment"
        APP = "app", "App"
        CREDENTIAL = "credential", "Credential"

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
    credential = models.ForeignKey(
        "IntegrationSharedCredential", on_delete=models.CASCADE, null=True, blank=True, related_name="tags",
    )
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(resource_type="workspace", workspace__isnull=False, environment__isnull=True, app__isnull=True, credential__isnull=True)
                    | models.Q(resource_type="environment", workspace__isnull=True, environment__isnull=False, app__isnull=True, credential__isnull=True)
                    | models.Q(resource_type="app", workspace__isnull=True, environment__isnull=True, app__isnull=False, credential__isnull=True)
                    | models.Q(resource_type="credential", workspace__isnull=True, environment__isnull=True, app__isnull=True, credential__isnull=False)
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
        CREDENTIAL = "credential", "Credential"

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
        from humanityrules_app.services import abac_service
        abac_service.validate_policy_conditions(
            identity_conditions=self.identity_conditions or [],
            resource_conditions=self.resource_conditions or [],
        )

    def __str__(self) -> str:
        return self.name


class EnvironmentBearerToken(models.Model):
    """
    Per-environment bearer token used by components running inside a customer
    env (policy proxies, Hermes, future env-resident services) to authenticate
    calls to HUMR's control plane. One active token per environment; the raw
    token lives in the customer's AWS Secrets Manager
    (humr/{env-slug}/shared-secrets under key HUMR_ENV_BEARER; namespaced per
    org as humr/sandbox/{org-slug}/shared-secrets in the shared sandbox). Only
    the hash is stored here so HUMR can authenticate incoming control-plane calls
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
    """HUMR-global config for a third-party integration provider.

    One row per provider. `config` holds the provider-specific payload as-is —
    e.g. for Google, the contents of the `web` object from the OAuth client
    JSON downloaded from Google Cloud Console.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        X = "x", "X"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    provider = models.CharField(max_length=50, choices=Provider.choices, unique=True)
    config = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Integration Config"
        verbose_name_plural = "Integration Configs"

    def __str__(self) -> str:
        return f"IntegrationConfig({self.provider})"


class IntegrationUserCredential(models.Model):
    """User-owned integration credentials for one logical app in one environment."""

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        GITHUB = "github", "GitHub"
        TELEGRAM = "telegram", "Telegram"
        SLACK = "slack", "Slack"
        OPENAI_CODEX = "openai-codex", "OpenAI Codex"
        OPENROUTER = "openrouter", "OpenRouter"
        NOUS = "nous", "Nous Portal"
        OPENAI = "openai-api", "OpenAI API"
        ANTHROPIC = "anthropic", "Anthropic"
        X = "x", "X"
        BROWSERUSE = "browseruse", "Browser Use"
        TAVILY = "tavily", "Tavily"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner_user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="integration_credentials",
    )
    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="integration_credentials",
    )
    app_slug = models.SlugField(max_length=255)
    provider = models.CharField(max_length=50, choices=Provider.choices)
    credentials = models.JSONField(
        default=dict,
        help_text="Secret provider-owned values supplied by the user, such as refresh tokens or API keys.",
    )
    config = models.JSONField(
        default=dict,
        help_text="Non-secret provider configuration for this logical app.",
    )
    metadata = models.JSONField(
        default=dict,
        help_text="Derived display/status data such as bot usernames or granted scopes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Integration User Credential"
        verbose_name_plural = "Integration User Credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["owner_user", "environment", "app_slug", "provider"],
                name="unique_owner_env_appslug_provider",
            ),
        ]

    def __str__(self) -> str:
        return f"IntegrationUserCredential({self.owner_user.username}@{self.environment.slug}/{self.app_slug}:{self.provider})"


class IntegrationSharedCredential(models.Model):
    """An org-provisioned integration credential an admin shares with users.

    The control plane stores one key here and an administrator chooses who
    receives it (a user, a workspace, or everybody). Hermes brokers fetch it
    automatically — when one applies it shadows the user's own pasted key
    (organization wins). Authorization is full ABAC: this is the ``credential``
    resource type with action ``credential:use``. The ``scope``/``target_*``
    columns are the canonical write-model and seed the ResourceTags the engine
    matches against (see ``abac_service.sync_shared_credential_tags``).
    """

    class Scope(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        USER = "user", "User"
        WORKSPACE = "workspace", "Workspace"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "Organization", on_delete=models.CASCADE, related_name="shared_integration_credentials",
    )
    provider = models.CharField(max_length=50, choices=IntegrationUserCredential.Provider.choices)
    scope = models.CharField(max_length=20, choices=Scope.choices)
    target_workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True,
        related_name="shared_integration_credentials",
        help_text="Set only when scope=workspace; the workspace whose apps receive this credential.",
    )
    target_user = models.ForeignKey(
        "User", on_delete=models.CASCADE, null=True, blank=True,
        related_name="received_shared_credentials",
        help_text="Set only when scope=user; the user whose apps receive this credential.",
    )
    credentials = models.JSONField(
        default=dict,
        blank=True,
        help_text="Secret provider-owned values supplied by the administrator, such as API keys.",
    )
    config = models.JSONField(default=dict, blank=True, help_text="Non-secret provider configuration.")
    metadata = models.JSONField(default=dict, blank=True, help_text="Derived display/status data such as validation timestamps.")
    token_cache = models.JSONField(
        default=dict,
        blank=True,
        help_text="Control-plane cache of the last exchanged access token for OAuth shares: {secrets, expires_at}.",
    )
    created_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The administrator who provisioned this shared credential.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Integration Shared Credential"
        verbose_name_plural = "Integration Shared Credentials"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="everyone", target_workspace__isnull=True, target_user__isnull=True)
                    | models.Q(scope="user", target_user__isnull=False, target_workspace__isnull=True)
                    | models.Q(scope="workspace", target_workspace__isnull=False, target_user__isnull=True)
                ),
                name="shared_credential_scope_target_consistent",
            ),
            models.UniqueConstraint(
                fields=["organization", "provider"],
                condition=models.Q(scope="everyone"),
                name="uniq_shared_credential_everyone_per_provider",
            ),
            models.UniqueConstraint(
                fields=["organization", "provider", "target_user"],
                condition=models.Q(scope="user"),
                name="uniq_shared_credential_user_per_provider",
            ),
            models.UniqueConstraint(
                fields=["organization", "provider", "target_workspace"],
                condition=models.Q(scope="workspace"),
                name="uniq_shared_credential_workspace_per_provider",
            ),
        ]

    def __str__(self) -> str:
        return f"IntegrationSharedCredential({self.organization.slug}:{self.provider}:{self.scope})"


class PlatformSharedCredential(models.Model):
    """A HumR-provisioned integration credential shared with every customer org.

    The platform tier sits at the bottom of the resolution ladder
    (``org-shared > personal > platform``): a broker receives one only where
    neither an org-shared nor a personal credential carries a usable secret.
    Unlike ``IntegrationSharedCredential`` it has no org targeting and is never
    ABAC-evaluated — it is global by construction (v1 audience is all orgs).
    HumR staff manage it through the Django admin. See
    ``docs/platform_shared_credentials_design.md``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    provider = models.CharField(max_length=50, choices=IntegrationUserCredential.Provider.choices)
    credentials = models.JSONField(
        default=dict,
        blank=True,
        help_text="Secret provider-owned values supplied by HumR staff, such as API keys.",
    )
    config = models.JSONField(default=dict, blank=True, help_text="Non-secret provider configuration.")
    metadata = models.JSONField(default=dict, blank=True, help_text="Derived display/status data such as validation timestamps.")
    token_cache = models.JSONField(
        default=dict,
        blank=True,
        help_text="Control-plane cache of the last exchanged access token for OAuth shares: {secrets, expires_at}.",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="When false the share is inert — kept for history without being handed to brokers.",
    )
    created_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The HumR staff member who provisioned this platform credential.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Shared Credential"
        verbose_name_plural = "Platform Shared Credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["provider"],
                condition=models.Q(enabled=True),
                name="uniq_enabled_platform_credential_per_provider",
            ),
        ]

    def __str__(self) -> str:
        return f"PlatformSharedCredential({self.provider}:{'enabled' if self.enabled else 'disabled'})"


# =============================================================================
# Cost subsystem models
# =============================================================================
# The logic lives in the self-contained humanityrules_app/services/cost/ package; the models sit here
# with the rest of the app's tables. See docs/app_cost_tracking_design.md.


class AppDailyCost(models.Model):
    """One per-day cost figure, keyed ``(app, environment, source, date, subkey)``.

    Source-agnostic by design: Bedrock writes ``source="bedrock"``, ``subkey=<normalized model id>``.
    Source-specific detail (token counts, flags) lives in ``details`` (JSON), never as columns, so the
    table stays generic. A row with ``subkey=""`` is a presence sentinel marking that the
    ``(env, date)`` was computed (possibly with zero cost) — it keeps gap detection unambiguous and is
    excluded from the per-model chart. Frozen rows (``is_final``) are never re-queried.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "humanityrules_app.Organization", on_delete=models.CASCADE, related_name="app_daily_costs",
    )
    app = models.ForeignKey("humanityrules_app.App", on_delete=models.CASCADE, related_name="daily_costs")
    environment = models.ForeignKey(
        "humanityrules_app.Environment", on_delete=models.CASCADE, related_name="app_daily_costs",
    )
    source = models.CharField(max_length=32, help_text="Cost source key, e.g. 'bedrock'.")
    date = models.DateField(help_text="UTC calendar day this cost is attributed to.")
    subkey = models.CharField(
        max_length=255,
        help_text="Source-specific sub-dimension (Bedrock: normalized model id). '' = presence sentinel.",
    )
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    details = models.JSONField(default=dict, help_text="Source-specific detail (token counts, flags).")
    is_final = models.BooleanField(
        default=False,
        help_text="Frozen: the day has aged past the lag window and is never re-queried.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App Daily Cost"
        verbose_name_plural = "App Daily Costs"
        constraints = [
            models.UniqueConstraint(
                fields=["app", "environment", "source", "date", "subkey"],
                name="unique_app_daily_cost",
            ),
        ]
        indexes = [
            models.Index(fields=["app", "date"]),
            models.Index(fields=["organization", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.app_id} {self.source}/{self.subkey} {self.date} ${self.cost_usd}"


class CostRefreshJob(models.Model):
    """A queued recompute of one app's costs, claimed and run by the job worker.

    Mirrors the repo's other async tasks: a row in ``PENDING`` is claimed via
    ``select_for_update(skip_locked=True)`` filtered by ``app__label``, transitioned to ``RUNNING``,
    then to a terminal status by the executor. The HTMX cost panel polls until the latest job for the
    app is terminal. ``result`` carries the recomputed rolling-24h figure (kept distinct from the
    calendar-day bins).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    TERMINAL_STATUSES = (Status.SUCCEEDED, Status.FAILED)
    ACTIVE_STATUSES = (Status.PENDING, Status.RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "humanityrules_app.Organization", on_delete=models.CASCADE, related_name="cost_refresh_jobs",
    )
    app = models.ForeignKey("humanityrules_app.App", on_delete=models.CASCADE, related_name="cost_refresh_jobs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_message = models.TextField(blank=True)
    claimed_by_run = models.ForeignKey(
        "humanityrules_app.JobWorkerRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Worker run that claimed this job; liveness input for stale-job detection",
    )
    result = models.JSONField(
        default=dict,
        help_text="Recompute summary, e.g. {'rolling_24h_usd': '1.23', 'rolling_24h_by_source': {...}}.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cost Refresh Job"
        verbose_name_plural = "Cost Refresh Jobs"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["app"],
                condition=models.Q(status__in=("pending", "running")),
                name="unique_active_cost_refresh_per_app",
            ),
        ]
        indexes = [
            models.Index(fields=["app", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"CostRefreshJob {self.id} app={self.app_id} {self.status}"


class JobWorkerRun(models.Model):
    """One incarnation of a job worker process, heartbeating while alive.

    Job rows stamp ``claimed_by_run`` on claim; a claimed executing row whose
    run stopped heartbeating has no live thread behind it and is reaped fast,
    without waiting out the no-progress timeout.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    label = models.CharField(max_length=64, blank=True, help_text="Worker scope label; empty = unscoped main worker")
    started_at = models.DateTimeField(auto_now_add=True)
    heartbeat_at = models.DateTimeField(help_text="Last proof of life")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"JobWorkerRun {self.id} (label={self.label!r})"


class WebappPublicGrant(models.Model):
    """Anonymous-internet access to one agent webapp.

    A live row makes ``https://<slug>-<agent-host>/`` reachable without a
    session: the PDP answers allow for that host and the policy proxy forwards
    the request with no ``X-Auth-*`` identity headers. Org scoping is
    transitive via ``app``.

    Rows are never deleted — revoking stamps ``revoked_at``/``revoked_by`` so
    exposure history stays queryable (same pattern as ``OrganizationInvite``).
    Each row is one continuous exposure window: extending a live grant updates
    ``expires_at``; re-publishing after expiry revokes the old row and inserts
    a fresh one. At most one non-revoked row exists per (app, slug). Expiry is
    evaluated lazily at PDP query time; no background job.
    """

    # User webapp slugs as accepted by the agent's `webapps` CLI, minus the
    # `__` platform-internal prefix: internal webapps are path-routed on the
    # bare agent host, which is never grantable.
    SLUG_PATTERN_TEXT = r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="webapp_public_grants")
    slug = models.CharField(max_length=32, help_text="Webapp hostname prefix, e.g. 'dashboard' in dashboard-<agent-host>.")
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="webapp_grants_created")
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Null = public until revoked.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="webapp_grants_revoked")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Webapp Public Grant"
        verbose_name_plural = "Webapp Public Grants"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["app", "slug"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_unrevoked_webapp_grant",
            ),
        ]
        indexes = [
            models.Index(fields=["app", "slug"], name="webapp_grant_app_slug_idx"),
        ]

    @classmethod
    def live(cls) -> "models.QuerySet[WebappPublicGrant]":
        """Grants currently granting access: not revoked and not expired."""
        return cls.objects.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()),
        )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())

    def __str__(self) -> str:
        return f"WebappPublicGrant {self.slug} app={self.app_id}"


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
            description="Your starting workspace for agents. Rename or create additional workspaces to organize by team, project, or department.",
        )


@receiver(post_save, sender=Organization)
def create_humr_sandbox_aws_account(
    sender: type[Organization],
    instance: Organization,
    created: bool,
    **kwargs: object,
) -> None:
    """Give a new Organization a connected 'Humanity Rules Sandbox' AWS account, if configured."""
    if not created:
        return
    from humanityrules_app.services import sandbox_service
    sandbox_service.ensure_org_sandbox(organization=instance)


@receiver(post_save, sender=Workspace)
def create_default_workspace_tag(
    sender: type[Workspace],
    instance: Workspace,
    created: bool,
    **kwargs: object,
) -> None:
    """Create a workspace-name tag when a new Workspace is created."""
    if created:
        from humanityrules_app.services import abac_service
        abac_service.create_default_workspace_tag(instance)


@receiver(post_save, sender=Environment)
def create_default_environment_tag(
    sender: type[Environment],
    instance: Environment,
    created: bool,
    **kwargs: object,
) -> None:
    """Create an environment-name tag when a new Environment is created."""
    if created:
        from humanityrules_app.services import abac_service
        abac_service.create_default_environment_tag(instance)


@receiver(post_save, sender=App)
def create_default_app_policy(
    sender: type[App],
    instance: App,
    created: bool,
    **kwargs: object,
) -> None:
    """Create a default app:use policy and app-name tag when a new App is created."""
    if created:
        from humanityrules_app.services.abac_service import create_default_app_policy as _create_policy
        _create_policy(instance)


@receiver(post_save, sender=IntegrationSharedCredential)
def sync_shared_credential_tags(
    sender: type[IntegrationSharedCredential],
    instance: IntegrationSharedCredential,
    created: bool,
    **kwargs: object,
) -> None:
    """Re-seed the credential's ABAC tags whenever it is created or its scope/target changes."""
    from humanityrules_app.services import abac_service
    abac_service.sync_shared_credential_tags(instance)
