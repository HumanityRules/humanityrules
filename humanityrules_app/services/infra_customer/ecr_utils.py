"""
Docker utility functions for building and pushing images to ECR.
"""

import base64
import json
import logging
import os
from pathlib import Path
import subprocess

import boto3
from botocore.exceptions import ClientError

from . import ec2_builder_utils

logger = logging.getLogger(__name__)


def image_tag_exists(session: boto3.Session, ecr_repo_name: str, image_tag: str) -> bool:
    """Return True when the tag exists in the ECR repo (False when the repo itself is missing)."""
    ecr_client = session.client("ecr")
    try:
        ecr_client.describe_images(repositoryName=ecr_repo_name, imageIds=[{"imageTag": image_tag}])
    except ClientError as e:
        if e.response["Error"]["Code"] in ("RepositoryNotFoundException", "ImageNotFoundException"):
            return False
        raise
    return True


def apply_keep_last_n_lifecycle_policy(ecr_client, repository_name: str, max_image_count: int) -> None:
    """Put a keep-last-N-by-push-date lifecycle policy onto an existing ECR repo."""
    policy = {
        "rules": [{
            "rulePriority": 1,
            "description": f"Keep last {max_image_count} images",
            "selection": {
                "tagStatus": "any",
                "countType": "imageCountMoreThan",
                "countNumber": max_image_count,
            },
            "action": {"type": "expire"},
        }],
    }
    ecr_client.put_lifecycle_policy(
        repositoryName=repository_name,
        lifecyclePolicyText=json.dumps(policy),
    )


def _stream_output(stream, level: int, source: str, stream_name: str) -> str:
    """Log each line from a subprocess stream and return the collected output."""
    if stream is None:
        return ""
    output_lines: list[str] = []
    for line in stream:
        cleaned = line.rstrip("\n")
        if not cleaned:
            continue
        output_lines.append(cleaned)
        logger.log(
            level,
            "%(line)s",
            {"line": cleaned},
            extra={"source": source, "stream": stream_name},
        )
    return "\n".join(output_lines)


def delete_all_ecr_images(session: boto3.Session, ecr_repo_name: str) -> bool:
    """
    Delete all images from an ECR repository.
    
    This is needed before deleting an ECR CloudFormation stack,
    as CloudFormation cannot delete a non-empty repository.
    
    Returns True on success, False on failure.
    """
    ecr_client = session.client("ecr")
    
    logger.info("Emptying ECR repository '%(ecr_repo_name)s'", {"ecr_repo_name": ecr_repo_name})
    
    try:
        # List all images in the repository
        paginator = ecr_client.get_paginator("list_images")
        image_ids = []
        
        for page in paginator.paginate(repositoryName=ecr_repo_name):
            image_ids.extend(page.get("imageIds", []))
        
        if not image_ids:
            logger.info("Repository is already empty")
            return True

        logger.info("Deleting %(image_count)s images", {"image_count": len(image_ids)})
        
        # Delete images in batches of 100 (AWS limit)
        for i in range(0, len(image_ids), 100):
            batch = image_ids[i:i + 100]
            ecr_client.batch_delete_image(
                repositoryName=ecr_repo_name,
                imageIds=batch,
            )
        
        logger.info(
            "Deleted %(image_count)s images from '%(ecr_repo_name)s'",
            {"image_count": len(image_ids), "ecr_repo_name": ecr_repo_name},
        )
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            logger.info("Repository '%(ecr_repo_name)s' does not exist, skipping", {"ecr_repo_name": ecr_repo_name})
            return True
        logger.error("Failed to empty repository: %(error)s", {"error": str(e)})
        return False


def build_and_push_docker_image(
    session: boto3.Session,
    account_id: str,
    region: str,
    env_slug: str,
    app_name: str,
    ecr_repo_name: str,
    app_source_path: Path | None,
    image_tag: str,
    build_id: str,
) -> str | None:
    """
    Build Docker image and push to ECR.

    Uses local Docker in development (HUMR_USE_REMOTE_BUILDER unset) or
    remote EC2 builder in production (HUMR_USE_REMOTE_BUILDER=1).

    Args:
        session: Boto3 session with credentials for the target account
        account_id: AWS account ID
        region: AWS region
        env_slug: Environment slug (needed to find EC2 builder in remote mode)
        app_name: Application name (for logging)
        ecr_repo_name: ECR repository name (e.g., "humr/default/simple-dashboard")
        app_source_path: Path to the app source directory containing Dockerfile
        image_tag: Docker image tag (e.g., "latest", "v1.0.0")
        build_id: Unique id isolating this build's /build dir on the shared EC2
            builder. Must be unique per attempt — two deploys racing to build
            the same tag then waste work instead of corrupting each other.

    Returns:
        The full image URI on success, None on failure.
    """
    logger.info("Building and pushing Docker image")
    logger.info("   App: %(app_name)s", {"app_name": app_name})
    logger.info("   Source: %(app_source_path)s", {"app_source_path": str(app_source_path)})
    logger.info("   Repository: %(ecr_repo_name)s", {"ecr_repo_name": ecr_repo_name})
    logger.info("   Tag: %(image_tag)s", {"image_tag": image_tag})

    if not app_source_path:
        logger.error("No app source path configured")
        return None

    # Full image URI
    image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repo_name}:{image_tag}"

    # Choose build method based on environment
    if os.environ.get("HUMR_USE_REMOTE_BUILDER") == "1":
        logger.info("Using remote EC2 builder")
        return _build_and_push_remote(
            session=session,
            env_slug=env_slug,
            app_source_path=app_source_path,
            image_uri=image_uri,
            build_id=build_id,
        )
    else:
        logger.info("Using local Docker")
        return _build_and_push_local(
            session=session,
            app_source_path=app_source_path,
            image_uri=image_uri,
        )


def test_docker_build(session: boto3.Session, env_slug: str, source_path: Path) -> tuple[bool, str]:
    """
    Test a Dockerfile by running docker build only (no push).

    Uses local Docker in development (HUMR_USE_REMOTE_BUILDER unset) or
    remote EC2 builder in production (HUMR_USE_REMOTE_BUILDER=1).

    Returns (success, build_output) tuple.
    """
    logger.info("Running test Docker build for %(source_path)s", {"source_path": str(source_path)})

    if os.environ.get("HUMR_USE_REMOTE_BUILDER") == "1":
        logger.info("Using remote EC2 builder for test build")
        return _test_build_remote(session=session, env_slug=env_slug, source_path=source_path)
    else:
        logger.info("Using local Docker for test build")
        return _test_build_local(source_path=source_path)


def _test_build_local(source_path: Path) -> tuple[bool, str]:
    """Test docker build locally — build only, no push."""
    import uuid
    tag = f"humr-test-build:{uuid.uuid4().hex[:8]}"

    process = subprocess.Popen(
        ["docker", "build", "--platform", "linux/arm64", "-t", tag, "."],
        cwd=source_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output = _stream_output(
        stream=process.stdout,
        level=logging.INFO,
        source="docker",
        stream_name="test-build",
    )
    process.wait()

    # Clean up test image regardless of outcome
    subprocess.run(["docker", "rmi", tag], capture_output=True)

    return process.returncode == 0, output


def _test_build_remote(session: boto3.Session, env_slug: str, source_path: Path) -> tuple[bool, str]:
    """Test docker build on remote EC2 builder — build only, no push."""
    import uuid
    build_id = f"test-{uuid.uuid4().hex[:8]}"

    try:
        instance_id = ec2_builder_utils.ensure_builder_running(session=session, env_slug=env_slug)

        if not ec2_builder_utils.wait_for_ssm_ready(session=session, instance_id=instance_id, timeout_seconds=180):
            return False, "SSM agent did not come online in time"

        if not ec2_builder_utils.transfer_source(session=session, instance_id=instance_id, source_path=source_path, build_id=build_id):
            return False, "Failed to transfer source code to builder"

        return ec2_builder_utils.run_remote_docker_build(session=session, instance_id=instance_id, image_uri=None, build_id=build_id)

    except Exception as e:
        logger.error("Remote test build failed: %(error)s", {"error": str(e)})
        return False, str(e)


def _build_and_push_local(session: boto3.Session, app_source_path: Path, image_uri: str) -> str | None:
    """Build and push Docker image using local Docker daemon."""
    # Build Docker image for ARM64 (Fargate supports ARM, avoids QEMU emulation issues on Apple Silicon)
    logger.info("   Building Docker image (platform: linux/arm64)")
    build_process = subprocess.Popen(
        ["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "."],
        cwd=app_source_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _stream_output(build_process.stdout, logging.INFO, "docker", "build")
    build_process.wait()

    if build_process.returncode != 0:
        logger.error("Docker build failed")
        return None

    logger.info("Docker image built: %(image_uri)s", {"image_uri": image_uri})

    # Get ECR login credentials
    ecr_client = session.client("ecr")

    try:
        auth_response = ecr_client.get_authorization_token()
        auth_data = auth_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":")
        registry_url = auth_data["proxyEndpoint"]

        logger.info("Got ECR authorization token")
    except ClientError as e:
        logger.error("Failed to get ECR auth token: %(error)s", {"error": str(e)})
        return None

    # Login to ECR
    logger.info("Logging into ECR")
    login_process = subprocess.Popen(
        ["docker", "login", "--username", username, "--password-stdin", registry_url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if login_process.stdin is not None:
        login_process.stdin.write(password)
        login_process.stdin.close()
    _stream_output(login_process.stdout, logging.INFO, "docker", "login")
    login_process.wait()

    if login_process.returncode != 0:
        logger.error("ECR login failed")
        return None

    logger.info("Logged into ECR")

    # Push image
    logger.info("Pushing image to ECR")
    push_process = subprocess.Popen(
        ["docker", "push", image_uri],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _stream_output(push_process.stdout, logging.INFO, "docker", "push")
    push_process.wait()

    if push_process.returncode != 0:
        logger.error("Docker push failed")
        return None

    logger.info("Image pushed to ECR: %(image_uri)s", {"image_uri": image_uri})

    return image_uri


def _build_and_push_remote(session: boto3.Session, env_slug: str, app_source_path: Path, image_uri: str, build_id: str) -> str | None:
    """Build and push Docker image using remote EC2 builder."""
    try:
        # Start EC2 builder if stopped
        instance_id = ec2_builder_utils.ensure_builder_running(session=session, env_slug=env_slug)

        # Wait for SSM agent to come online
        if not ec2_builder_utils.wait_for_ssm_ready(session=session, instance_id=instance_id, timeout_seconds=180):
            logger.error("SSM agent did not come online in time")
            return None

        # Transfer source code to builder (build_id isolates concurrent builds' /build dirs)
        if not ec2_builder_utils.transfer_source(session=session, instance_id=instance_id, source_path=app_source_path, build_id=build_id):
            logger.error("Failed to transfer source code to builder")
            return None

        # Run Docker build and push
        success, logs = ec2_builder_utils.run_remote_docker_build(session=session, instance_id=instance_id, image_uri=image_uri, build_id=build_id)
        if not success:
            logger.error("Remote Docker build failed: %(logs)s", {"logs": logs})
            return None

        logger.info("Image pushed to ECR: %(image_uri)s", {"image_uri": image_uri})
        return image_uri

    except Exception as e:
        logger.error("Remote build failed: %(error)s", {"error": str(e)})
        return None

