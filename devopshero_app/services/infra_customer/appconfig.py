"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class EngineConfig:
    """Aurora engine configuration."""
    family: str  # "aurora-mysql" or "aurora-postgresql"
    version: str | None  # Version catalog key, None = default
    auto_minor_version_upgrade: bool


@dataclass
class ServerlessV2Config:
    """Aurora Serverless v2 scaling configuration."""
    min_acu: float
    max_acu: float


@dataclass
class ProvisionedConfig:
    """Aurora provisioned instance configuration."""
    instance_class: str  # e.g., "db.r6g.large"


@dataclass
class DeploymentConfig:
    """Aurora deployment mode configuration."""
    mode: str  # "aurora_serverless_v2" or "aurora_provisioned"
    serverless_v2: ServerlessV2Config | None
    provisioned: ProvisionedConfig | None


@dataclass
class BackupConfig:
    """Backup configuration."""
    retention_days: int
    copy_tags_to_snapshot: bool


@dataclass
class SecurityConfig:
    """Security settings."""
    storage_encrypted: bool
    deletion_protection: bool


@dataclass
class ConnectionConfig:
    """Connection injection settings."""
    env_var_name: str | None


@dataclass
class EfsConfig:
    """EFS volume configuration for the container."""
    mount_path: str
    posix_uid: int
    posix_gid: int


@dataclass
class DatabaseConfig:
    """Configuration for an Aurora database."""
    name: str  # Database name, e.g., "myapp_prod"
    engine: EngineConfig
    deployment: DeploymentConfig
    backups: BackupConfig
    security: SecurityConfig
    connection: ConnectionConfig


@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""

    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" - used in resource names
    ecr_repo_name: str  # e.g., "doh/default/simple-dashboard"

    # Container configuration
    container_port: int
    cpu: int  # Fargate CPU units (256, 512, 1024, etc.)
    memory: int  # Fargate memory in MiB

    # Health checks
    health_check_path: str  # For ALB health checks
    health_check_command: str | None  # For container health checks (CMD-SHELL)

    # Environment variables as list of {"name": str, "value": str}
    environment_variables: list[dict[str, str]]

    # Local paths
    app_source_path: Path | None  # Path to app source for Docker build

    # Database configuration (None = no database)
    database_config: DatabaseConfig | None = None

    # App secrets configuration (optional - for apps that read secrets from Secrets Manager)
    # Keys are secret field names, values are either:
    #   - str: use this literal value
    #   - None: generate a random 64-char alphanumeric value
    # Example: {"slack_token": "disabled", "secret_key_base": None, "signing_salt": None}
    app_secrets: dict[str, str | None] | None = None

    # Override ECS health check grace period (seconds). None = use environment default.
    health_check_grace_period: int | None = None

    # EFS volume configuration (None = no EFS volume)
    efs_config: EfsConfig | None = None

    def to_template_vars(self) -> dict:
        """Convert to dict for Jinja2 template rendering (CloudFormation)."""
        return {
            "app_name": self.app_name,
            "ecr_repo_name": self.ecr_repo_name,
            "container_port": self.container_port,
            "cpu": str(self.cpu),  # CloudFormation expects strings
            "memory": str(self.memory),  # CloudFormation expects strings
            "health_check_path": self.health_check_path,
            "health_check_command": self.health_check_command,
            "environment_variables": self.environment_variables,
        }
