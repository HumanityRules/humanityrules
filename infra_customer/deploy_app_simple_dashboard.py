#!/usr/bin/env python3
"""
Deploy the simple-dashboard app to a customer AWS account.

This is the unified entry point for deploying simple-dashboard using either
CloudFormation templates (cf) or AWS CDK (cdk).

Usage:
    cd infra_customer
    uv run python deploy_app_simple_dashboard.py                     # Deploy with CF (default)
    uv run python deploy_app_simple_dashboard.py --engine cdk        # Deploy with CDK
    uv run python deploy_app_simple_dashboard.py --image-tag v1.2.3  # Deploy with specific image tag
    uv run python deploy_app_simple_dashboard.py --engine cdk --synth-only  # Only synthesize CDK templates
    uv run python deploy_app_simple_dashboard.py --teardown          # Delete all stacks

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


def get_simple_dashboard_config() -> AppConfig:
    """Return the AppConfig for simple-dashboard."""
    return AppConfig(
        app_name="simple-dashboard",
        ecr_repo_name="devopshero/simple-dashboard",
        container_port=8501,
        cpu=256,
        memory=512,
        health_check_path="/_stcore/health",
        health_check_command='python -c "import urllib.request; urllib.request.urlopen(\'http://localhost:8501/_stcore/health\', timeout=5)" || exit 1',
        environment_variables=[
            {"name": "STREAMLIT_SERVER_PORT", "value": "8501"},
            {"name": "STREAMLIT_SERVER_ADDRESS", "value": "0.0.0.0"},
            {"name": "STREAMLIT_SERVER_HEADLESS", "value": "true"},
            {"name": "STREAMLIT_BROWSER_GATHER_USAGE_STATS", "value": "false"},
        ],
        app_source_path=Path(__file__).parent.parent / "deployable_repos" / "simple_dashboard",
        domain_name="simple-dashboard.chsandbox.com",
        hosted_zone_name="chsandbox.com",
    )


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Deploy simple-dashboard to AWS"
    )
    parser.add_argument(
        "--engine",
        choices=["cf", "cdk"],
        default="cf",
        help="Deployment engine: cf (CloudFormation) or cdk (AWS CDK). Default: cf",
    )
    parser.add_argument(
        "--image-tag",
        default="latest",
        help="Docker image tag (default: latest)",
    )
    parser.add_argument(
        "--synth-only",
        action="store_true",
        help="(CDK only) Only synthesize templates, don't deploy",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Delete all stacks instead of deploying",
    )
    args = parser.parse_args()

    # Validate args
    if args.synth_only and args.engine != "cdk":
        print("❌ --synth-only is only valid with --engine cdk")
        sys.exit(1)
    
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
    app_config = get_simple_dashboard_config()

    # Dispatch to the appropriate engine (lazy import to avoid loading CDK when not needed)
    if args.teardown:
        if args.engine == "cf":
            import deploy_app_cf
            success = deploy_app_cf.teardown(session=session, app_config=app_config)
        else:
            import deploy_app_cdk
            success = deploy_app_cdk.teardown(session=session, app_config=app_config)
    elif args.engine == "cf":
        import deploy_app_cf
        success = deploy_app_cf.deploy(
            session=session,
            account_id=TARGET_ACCOUNT_ID,
            region=TARGET_REGION,
            app_config=app_config,
            image_tag=args.image_tag,
        )
    else:
        import deploy_app_cdk
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

