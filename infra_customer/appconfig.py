"""
Application configuration dataclass for ECS deployments.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""

    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" - used in resource names
    ecr_repo_name: str  # e.g., "devopshero/simple-dashboard"

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

    # Domain configuration (for HTTPS and Route53)
    domain_name: str | None  # e.g., "simple-dashboard.chsandbox.com"
    hosted_zone_name: str | None  # e.g., "chsandbox.com"

    # Database configuration (optional - for apps that need Aurora)
    needs_database: bool = False  # If True, creates Aurora Serverless v2
    database_name: str | None = None  # e.g., "db_portal_prod"

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
            "domain_name": self.domain_name,
            "hosted_zone_name": self.hosted_zone_name,
        }

