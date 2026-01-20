#!/usr/bin/env python3
"""
Unified deployment entry point for DevOpsHero infrastructure and apps.

Usage:
    cd infra_customer

    # Base layer (VPC + ECS cluster)
    uv run python deploy.py --base                       # Deploy base layer
    uv run python deploy.py --base --teardown            # Teardown base layer
    uv run python deploy.py --base --synth-only          # Synth only
    uv run python deploy.py --base --env prod            # Deploy to prod environment

    # Apps
    uv run python deploy.py --app simple-dashboard       # Deploy simple-dashboard
    uv run python deploy.py --app db-portal              # Deploy db-portal
    uv run python deploy.py --app simple-dashboard --image-tag v1.2.3  # Specific tag
    uv run python deploy.py --app simple-dashboard --synth-only        # Synth only
    uv run python deploy.py --app simple-dashboard --teardown          # Teardown app
    uv run python deploy.py --app simple-dashboard --env prod          # Deploy to prod

Required environment variables (from ../.env):
    DOH_AWS_ACCESS_KEY - DevOpsHero control plane AWS access key
    DOH_AWS_SECRET_KEY - DevOpsHero control plane AWS secret key
"""

import argparse
import os
import sys

from . import deploy_app
from . import deploy_base
from . import example_apps
from . import iam_utils


# Target account configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"


def main():
    parser = argparse.ArgumentParser(
        description="Deploy DevOpsHero infrastructure and apps"
    )

    # Mutually exclusive: --base or --app
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--base",
        action="store_true",
        help="Deploy/teardown base layer (VPC + ECS cluster)",
    )
    group.add_argument(
        "--app",
        choices=list(example_apps.APP_CONFIGS.keys()),
        help="Deploy/teardown a specific app",
    )

    # Common options
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Teardown instead of deploy",
    )
    parser.add_argument(
        "--synth-only",
        action="store_true",
        help="Only synthesize CDK templates, don't deploy",
    )
    parser.add_argument(
        "--env",
        default="default",
        help="Environment slug (default: 'default'). Controls resource naming and isolation.",
    )

    # App-specific options
    parser.add_argument(
        "--image-tag",
        default="latest",
        help="Docker image tag (default: latest). Only used with --app",
    )

    args = parser.parse_args()

    # Validate args
    if args.image_tag != "latest" and args.base:
        print("--image-tag is only valid with --app")
        sys.exit(1)

    if args.synth_only and args.teardown:
        print("--synth-only and --teardown are mutually exclusive")
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

    # Dispatch
    if args.base:
        if args.teardown:
            success = deploy_base.teardown(session=session, env_slug=args.env)
        else:
            success = deploy_base.deploy(
                session=session,
                env_slug=args.env,
                synth_only=args.synth_only,
                log_callback=None,
            )
    else:
        app_config = example_apps.get_app_config(
            app_name=args.app,
            env_slug=args.env,
        )

        if args.teardown:
            success = deploy_app.teardown(
                session=session,
                app_config=app_config,
                env_slug=args.env,
            )
        else:
            success = deploy_app.deploy(
                session=session,
                account_id=TARGET_ACCOUNT_ID,
                region=TARGET_REGION,
                app_config=app_config,
                image_tag=args.image_tag,
                env_slug=args.env,
                synth_only=args.synth_only,
                log_callback=None,
            )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
