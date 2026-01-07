"""
Docker utility functions for building and pushing images to ECR.
"""

import base64
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def delete_all_ecr_images(session: boto3.Session, ecr_repo_name: str) -> bool:
    """
    Delete all images from an ECR repository.
    
    This is needed before deleting an ECR CloudFormation stack,
    as CloudFormation cannot delete a non-empty repository.
    
    Returns True on success, False on failure.
    """
    ecr_client = session.client("ecr")
    
    print(f"   🗑️  Emptying ECR repository '{ecr_repo_name}'...")
    
    try:
        # List all images in the repository
        paginator = ecr_client.get_paginator("list_images")
        image_ids = []
        
        for page in paginator.paginate(repositoryName=ecr_repo_name):
            image_ids.extend(page.get("imageIds", []))
        
        if not image_ids:
            print(f"   ✅ Repository is already empty")
            return True
        
        print(f"   🗑️  Deleting {len(image_ids)} images...")
        
        # Delete images in batches of 100 (AWS limit)
        for i in range(0, len(image_ids), 100):
            batch = image_ids[i:i + 100]
            ecr_client.batch_delete_image(
                repositoryName=ecr_repo_name,
                imageIds=batch,
            )
        
        print(f"   ✅ Deleted {len(image_ids)} images from '{ecr_repo_name}'")
        return True
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            print(f"   ⏭️  Repository '{ecr_repo_name}' does not exist, skipping")
            return True
        print(f"   ❌ Failed to empty repository: {e}")
        return False


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

    # Build Docker image for ARM64 (Fargate supports ARM, avoids QEMU emulation issues on Apple Silicon)
    print(f"   ⏳ Building Docker image (platform: linux/arm64)...")
    build_result = subprocess.run(
        ["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "."],
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

