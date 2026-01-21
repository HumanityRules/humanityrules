"""
Docker utility functions for building and pushing images to ECR.
"""

import base64
import logging
from pathlib import Path
import subprocess
import threading

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _stream_output(stream, level: int, source: str, stream_name: str) -> None:
    if stream is None:
        return
    for line in stream:
        cleaned = line.rstrip("\n")
        if not cleaned:
            continue
        logger.log(
            level,
            "%(line)s",
            {"line": cleaned},
            extra={"source": source, "stream": stream_name},
        )


def _stream_process_output(process: subprocess.Popen[str], source: str) -> None:
    stdout_thread = threading.Thread(
        target=_stream_output,
        args=(process.stdout, logging.INFO, source, "stdout"),
    )
    stderr_thread = threading.Thread(
        target=_stream_output,
        args=(process.stderr, logging.ERROR, source, "stderr"),
    )
    stdout_thread.start()
    stderr_thread.start()
    process.wait()
    stdout_thread.join()
    stderr_thread.join()


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
        ecr_repo_name: ECR repository name (e.g., "doh/default/simple-dashboard")
        app_source_path: Path to the app source directory containing Dockerfile
        image_tag: Docker image tag (e.g., "latest", "v1.0.0")

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

    # Build Docker image for ARM64 (Fargate supports ARM, avoids QEMU emulation issues on Apple Silicon)
    logger.info("   Building Docker image (platform: linux/arm64)")
    build_process = subprocess.Popen(
        ["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "."],
        cwd=app_source_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _stream_process_output(process=build_process, source="docker")

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
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if login_process.stdin is not None:
        login_process.stdin.write(password)
        login_process.stdin.close()
    _stream_process_output(process=login_process, source="docker")

    if login_process.returncode != 0:
        logger.error("ECR login failed")
        return None

    logger.info("Logged into ECR")

    # Push image
    logger.info("Pushing image to ECR")
    push_process = subprocess.Popen(
        ["docker", "push", image_uri],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _stream_process_output(process=push_process, source="docker")

    if push_process.returncode != 0:
        logger.error("Docker push failed")
        return None

    logger.info("Image pushed to ECR: %(image_uri)s", {"image_uri": image_uri})

    return image_uri

