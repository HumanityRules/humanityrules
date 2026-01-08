"""
CDK utility functions for deploying stacks.
"""

import os
import subprocess
from pathlib import Path

import boto3
from aws_cdk import App

CDK_OUT_DIR = Path(__file__).parent / "cdk.out"


def deploy_cdk_stacks(app: App, session: boto3.Session) -> bool:
    """Synthesize and deploy CDK stacks using the CDK CLI."""
    print(f"\n{'='*60}")
    print(f"📦 Synthesizing and deploying CDK stacks...")
    print(f"{'='*60}")

    credentials = session.get_credentials()
    frozen_credentials = credentials.get_frozen_credentials()

    cdk_env = os.environ.copy()
    cdk_env["AWS_ACCESS_KEY_ID"] = frozen_credentials.access_key
    cdk_env["AWS_SECRET_ACCESS_KEY"] = frozen_credentials.secret_key
    if frozen_credentials.token:
        cdk_env["AWS_SESSION_TOKEN"] = frozen_credentials.token

    cloud_assembly = app.synth()

    print(f"   Synthesized CDK stacks to: {cloud_assembly.directory}")
    print(f"   Deploying CDK stacks using the CDK CLI...")

    deploy_result = subprocess.run(
        ["npx", "cdk", "deploy", "--all", "--require-approval", "never", "--no-notices", "--app", cloud_assembly.directory],
        env=cdk_env,
        capture_output=False,  # Show output in real-time
    )

    if deploy_result.returncode != 0:
        print(f"\n❌ CDK deployment failed")
        return False

    print(f"\n✅ CDK deployment complete")
    return True
