"""
Docker utility functions for building and pushing images to ECR.
"""

import base64
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def build_and_push_docker_image(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_name: str,
    ecr_repo_name: str,
    app_source_path: Path | None,
    image_tag: str,
) -> str | None:
    """
    Build Docker image and push to ECR.

    Args:
        session: Boto3 session with credentials for the target account
        account_id: AWS account ID
        region: AWS region
        app_name: Application name (for logging)
        ecr_repo_name: ECR repository name (e.g., "devopshero/simple-dashboard")
        app_source_path: Path to the app source directory containing Dockerfile
        image_tag: Docker image tag (e.g., "latest", "v1.0.0")

    Returns:
        The full image URI on success, None on failure.
    """
    print(f"{'='*60}")
    print(f"🐳 Building and pushing Docker image")
    print(f"   App: {app_name}")
    print(f"   Source: {app_source_path}")
    print(f"   Repository: {ecr_repo_name}")
    print(f"   Tag: {image_tag}")

    if not app_source_path:
        print(f"   ❌ No app source path configured")
        return None

    # Full image URI
    image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repo_name}:{image_tag}"

    # Build Docker image for AMD64 (Fargate runs on x86_64, not ARM)
    print(f"   ⏳ Building Docker image (platform: linux/amd64)...")
    build_result = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", image_uri, "."],
        cwd=app_source_path,
        capture_output=True,
        text=True,
    )

    if build_result.returncode != 0:
        print(f"   ❌ Docker build failed:")
        print(build_result.stderr)
        return None

    print(f"   ✅ Docker image built: {image_uri}")

    # Get ECR login credentials
    ecr_client = session.client("ecr")

    try:
        auth_response = ecr_client.get_authorization_token()
        auth_data = auth_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":")
        registry_url = auth_data["proxyEndpoint"]

        print(f"   ✅ Got ECR authorization token")
    except ClientError as e:
        print(f"   ❌ Failed to get ECR auth token: {e}")
        return None

    # Login to ECR
    print(f"   ⏳ Logging into ECR...")
    login_result = subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry_url],
        input=password,
        capture_output=True,
        text=True,
    )

    if login_result.returncode != 0:
        print(f"   ❌ ECR login failed:")
        print(login_result.stderr)
        return None

    print(f"   ✅ Logged into ECR")

    # Push image
    print(f"   ⏳ Pushing image to ECR...")
    push_result = subprocess.run(
        ["docker", "push", image_uri],
        capture_output=True,
        text=True,
    )

    if push_result.returncode != 0:
        print(f"   ❌ Docker push failed:")
        print(push_result.stderr)
        return None

    print(f"   ✅ Image pushed to ECR: {image_uri}")

    return image_uri

