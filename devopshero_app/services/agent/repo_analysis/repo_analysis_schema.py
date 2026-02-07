"""
Pydantic models for repository analysis output.

These models define the structured output that the repo analysis sub-agent produces.
The schema is designed to feed downstream steps (Dockerfile generation, deployment planning).
"""

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """Evidence supporting a claim in the analysis."""

    claim: str = Field(description="The claim this evidence supports (e.g., 'framework=django')")
    file_path: str = Field(description="Path to the file containing the evidence")
    excerpt: str = Field(description="Relevant excerpt from the file")


class EnvVar(BaseModel):
    """An environment variable detected in the repository."""

    name: str = Field(description="Environment variable name")
    purpose: str = Field(description="What this variable is used for")


class ServiceConfig(BaseModel):
    """Configuration for the deployable service."""

    type: Literal["web", "worker", "scheduled_task"] = Field(description="Service type")
    workdir: str = Field(description="Working directory within repo (usually '.')")
    run_command: str | None = Field(description="Command to start the service")
    port: int | None = Field(description="Listening port (web services only)")
    healthcheck_path: str | None = Field(description="Health check endpoint path (web services only)")


class DependenciesConfig(BaseModel):
    """Infrastructure dependencies detected in the repository."""

    datastores: list[str] = Field(description="Required datastores (postgres, redis, mysql, etc.)")
    aws_services: list[str] = Field(description="Required AWS services (s3, sqs, dynamodb, etc.)")


class EnvConfig(BaseModel):
    """Environment variable configuration."""

    required: list[EnvVar] = Field(description="Required environment variables")
    optional: list[EnvVar] = Field(description="Optional environment variables")


class SecretField(BaseModel):
    """A secret field the app expects from AWS Secrets Manager."""

    name: str = Field(description="Secret field name")
    purpose: str = Field(description="What this secret is used for")
    default_behavior: str | None = Field(description="What happens if missing (e.g., 'defaults to disabled')")


class SecretsConfig(BaseModel):
    """AWS Secrets Manager configuration detected in the repository."""

    secret_path: str = Field(description="Secret path pattern (e.g., 'devopshero/{app}/secrets')")
    fields: list[SecretField] = Field(description="All secret fields the app reads - include ALL detected fields")


class RepoAnalysisOutput(BaseModel):
    """Complete repository analysis output."""

    description: str = Field(description="Human-readable summary of the application (2-5 sentences)")
    language: str = Field(description="Primary programming language (python, node, elixir, go, etc.)")
    framework: str | None = Field(description="Detected framework (django, fastapi, nextjs, phoenix, etc.)")
    service: ServiceConfig = Field(description="Service configuration")
    dependencies: DependenciesConfig = Field(description="Infrastructure dependencies")
    env: EnvConfig = Field(description="Environment variable configuration")
    secrets: SecretsConfig | None = Field(description="Secrets Manager config if detected, null otherwise")
    caveats: list[str] = Field(description="Warnings, concerns, or things the user should know")
    evidence: list[EvidenceItem] = Field(description="Evidence supporting major claims")
    dockerfile_path: str | None = Field(default=None, description="Path to existing Dockerfile relative to repo root (null if none found)")
