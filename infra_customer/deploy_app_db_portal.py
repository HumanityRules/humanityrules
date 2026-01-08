#!/usr/bin/env python3
"""
Deploy the db_portal app to a customer AWS account.

This is the entry point for deploying db_portal using AWS CDK.
db_portal is an Elixir/Phoenix application that requires:
- Aurora MySQL Serverless v2 database
- HTTP-only mode (TLS terminated at ALB)
- Authentication disabled (for initial testing)

Usage:
    cd infra_customer
    uv run python deploy_app_db_portal.py                     # Deploy with CDK
    uv run python deploy_app_db_portal.py --image-tag v1.2.3  # Deploy with specific image tag
    uv run python deploy_app_db_portal.py --synth-only        # Only synthesize CDK templates
    uv run python deploy_app_db_portal.py --teardown          # Delete all stacks

Required environment variables (from ../.env):
    DOH_AWS_ACCESS_KEY - DevOpsHero control plane AWS access key
    DOH_AWS_SECRET_KEY - DevOpsHero control plane AWS secret key
"""

import argparse
import os
import sys
from pathlib import Path

import iam_utils
from appconfig import AppConfig


# Target account configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"


def get_db_portal_config() -> AppConfig:
    """Return the AppConfig for db_portal."""
    return AppConfig(
        app_name="db-portal",
        ecr_repo_name="devopshero/db-portal",
        container_port=4000,  # HTTP port for ALB (app runs HTTP-only behind ALB)
        cpu=512,
        memory=1024,
        health_check_path="/health",  # Our custom health endpoint
        health_check_command=None,  # ALB health check is sufficient
        environment_variables=[
            # Elixir/Phoenix configuration
            {"name": "MIX_ENV", "value": "prod"},
            {"name": "PHX_SERVER", "value": "true"},
            {"name": "PORT", "value": "4000"},
            {"name": "PHX_HOST", "value": "dataengr.chsandbox.com"},
            # DevOpsHero ECS mode flags
            {"name": "DISABLE_HTTPS", "value": "true"},  # TLS terminated at ALB
            {"name": "DISABLE_AUTH", "value": "true"},   # Skip Okta for now
            {"name": "RUN_SAMPLER", "value": "N"},       # Don't run sampler scheduler
            # App reads secrets directly from Secrets Manager (devopshero/db-portal/secrets)
        ],
        app_source_path=Path(__file__).parent.parent / "deployable_repos" / "db_portal",
        domain_name="dataengr.chsandbox.com",
        hosted_zone_name="chsandbox.com",
        # Database configuration
        needs_database=True,
        database_name="db_portal_prod",
        # App secrets (read directly from Secrets Manager at runtime)
        # None values will be auto-generated as random 64-char strings
        app_secrets={
            "slack_token": "disabled",
            "secret_key_base": None,  # Will be generated
            "signing_salt": None,     # Will be generated
        },
    )


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Deploy db_portal to AWS"
    )
    parser.add_argument(
        "--image-tag",
        default="latest",
        help="Docker image tag (default: latest)",
    )
    parser.add_argument(
        "--synth-only",
        action="store_true",
        help="Only synthesize CDK templates, don't deploy",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Delete all stacks instead of deploying",
    )
    args = parser.parse_args()
    
    # Load credentials from .env
    iam_utils.load_credentials_from_env()

    # Get assumed role session into target account
    session = iam_utils.get_assumed_role_session(
        access_key=os.getenv("DOH_AWS_ACCESS_KEY"),
        secret_key=os.getenv("DOH_AWS_SECRET_KEY"),
        account_id=TARGET_ACCOUNT_ID,
        external_id=TARGET_EXTERNAL_ID,
        region=TARGET_REGION,
    )

    # Get app configuration
    app_config = get_db_portal_config()

    # Always use CDK for db_portal (it needs Aurora)
    import deploy_app_cdk
    
    if args.teardown:
        success = deploy_app_cdk.teardown(session=session, app_config=app_config)
    else:
        success = deploy_app_cdk.deploy(
            session=session,
            account_id=TARGET_ACCOUNT_ID,
            region=TARGET_REGION,
            app_config=app_config,
            image_tag=args.image_tag,
            synth_only=args.synth_only,
        )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()

