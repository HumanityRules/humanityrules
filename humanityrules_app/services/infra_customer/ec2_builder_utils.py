"""
EC2 Builder utilities for remote Docker builds.

This module manages the EC2 builder instance lifecycle and provides functions
for transferring source code and running Docker builds remotely via SSH-over-SSM.
"""

import logging
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)


def get_builder_instance_id(session: boto3.Session, env_slug: str) -> str | None:
    """Find builder instance by tag humr:environment={env_slug}."""
    ec2_client = session.client("ec2")

    response = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:humr:environment", "Values": [env_slug]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ],
    )

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = instance.get("InstanceId")
            if instance_id:
                logger.info(
                    "Found builder instance %(instance_id)s for environment %(env_slug)s",
                    {"instance_id": instance_id, "env_slug": env_slug},
                )
                return instance_id

    logger.info("No builder instance found for environment %(env_slug)s", {"env_slug": env_slug})
    return None


def ensure_builder_running(session: boto3.Session, env_slug: str) -> str:
    """Start EC2 builder if stopped, return instance ID. Wait for running state."""
    instance_id = get_builder_instance_id(session=session, env_slug=env_slug)
    if not instance_id:
        raise RuntimeError(f"No builder instance found for environment '{env_slug}'")

    ec2_client = session.client("ec2")

    # Check current state
    response = ec2_client.describe_instances(InstanceIds=[instance_id])
    state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
    logger.info("Builder instance %(instance_id)s is in state '%(state)s'", {"instance_id": instance_id, "state": state})

    if state == "running":
        return instance_id

    if state == "stopped":
        logger.info("Starting builder instance %(instance_id)s", {"instance_id": instance_id})
        ec2_client.start_instances(InstanceIds=[instance_id])
    elif state == "stopping":
        logger.info("Builder instance is stopping, waiting for stopped state before starting")
        _wait_for_instance_state(ec2_client=ec2_client, instance_id=instance_id, target_state="stopped", timeout_seconds=120)
        logger.info("Starting builder instance %(instance_id)s", {"instance_id": instance_id})
        ec2_client.start_instances(InstanceIds=[instance_id])
    elif state == "pending":
        logger.info("Builder instance is already starting")
    else:
        raise RuntimeError(f"Builder instance in unexpected state: {state}")

    # Wait for running state
    _wait_for_instance_state(ec2_client=ec2_client, instance_id=instance_id, target_state="running", timeout_seconds=180)
    logger.info("Builder instance %(instance_id)s is now running", {"instance_id": instance_id})

    return instance_id


def _wait_for_instance_state(ec2_client, instance_id: str, target_state: str, timeout_seconds: int) -> None:
    """Poll EC2 until instance reaches target state."""
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == target_state:
            return
        logger.info("Instance state: %(state)s, waiting for %(target_state)s", {"state": state, "target_state": target_state})
        time.sleep(5)
    raise TimeoutError(f"Instance {instance_id} did not reach state '{target_state}' within {timeout_seconds}s")


def wait_for_ssm_ready(session: boto3.Session, instance_id: str, timeout_seconds: int) -> bool:
    """Poll SSM until instance is registered and online."""
    ssm_client = session.client("ssm")

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        response = ssm_client.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
        )
        instances = response.get("InstanceInformationList", [])
        if instances and instances[0].get("PingStatus") == "Online":
            logger.info("SSM agent is online for instance %(instance_id)s", {"instance_id": instance_id})
            return True
        logger.info("Waiting for SSM agent to come online for instance %(instance_id)s", {"instance_id": instance_id})
        time.sleep(5)

    logger.error("SSM agent did not come online within %(timeout)ss", {"timeout": timeout_seconds})
    return False


def transfer_source(session: boto3.Session, instance_id: str, source_path: Path, build_id: str) -> bool:
    """Transfer source directory to EC2 via SSH-over-SSM using EC2 Instance Connect."""
    region = session.region_name
    build_dir = f"/build/{build_id}"

    # Generate ephemeral SSH keypair
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "id_ed25519"
        pub_key_path = Path(tmpdir) / "id_ed25519.pub"

        # Generate Ed25519 key
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
            check=True,
        )
        public_key = pub_key_path.read_text().strip()

        # Push public key via EC2 Instance Connect
        ec2ic_client = session.client("ec2-instance-connect")
        ec2ic_client.send_ssh_public_key(
            InstanceId=instance_id,
            InstanceOSUser="ec2-user",
            SSHPublicKey=public_key,
        )
        logger.info("Pushed ephemeral SSH key via EC2 Instance Connect")

        # Build SSH options for SSM proxy
        ssh_options = _build_ssh_options(key_path=key_path, region=region, session=session)

        # Create build directory on remote (rsync needs it to exist)
        ssh_cmd = ["ssh"] + ssh_options + [f"ec2-user@{instance_id}", f"mkdir -p {build_dir}"]
        subprocess.run(ssh_cmd, capture_output=True, text=True)

        # Transfer source using rsync over SSH (--delete removes stale files from previous builds)
        # Build the ssh command string with proper quoting for rsync -e
        logger.info("Transferring source to builder instance")
        ssh_cmd_parts = ["ssh"] + ssh_options
        ssh_cmd_str = " ".join(shlex.quote(part) for part in ssh_cmd_parts)

        rsync_cmd = [
            "rsync", "-avz", "--delete",
            "-e", ssh_cmd_str,
            f"{source_path}/",
            f"ec2-user@{instance_id}:{build_dir}/",
        ]
        result = subprocess.run(rsync_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("rsync failed: %(stderr)s", {"stderr": result.stderr})
            return False

        logger.info("Source transferred successfully to %(build_dir)s", {"build_dir": build_dir})
        return True


def run_remote_docker_build(session: boto3.Session, instance_id: str, image_uri: str | None, build_id: str) -> tuple[bool, str]:
    """
    SSH to EC2, run docker build, return (success, logs).

    When image_uri is provided: builds, pushes to ECR, and cleans up.
    When image_uri is None: build-only test, no push, discards the image.
    """
    region = session.region_name
    build_dir = f"/build/{build_id}"

    # Generate ephemeral SSH keypair
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "id_ed25519"
        pub_key_path = Path(tmpdir) / "id_ed25519.pub"

        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
            check=True,
        )
        public_key = pub_key_path.read_text().strip()

        # Push public key via EC2 Instance Connect
        ec2ic_client = session.client("ec2-instance-connect")
        ec2ic_client.send_ssh_public_key(
            InstanceId=instance_id,
            InstanceOSUser="ec2-user",
            SSHPublicKey=public_key,
        )
        logger.info("Pushed ephemeral SSH key for docker build")

        ssh_options = _build_ssh_options(key_path=key_path, region=region, session=session)

        if image_uri is not None:
            ecr_registry = image_uri.split("/")[0]
            build_script = f"""
set -e
cd {build_dir}
touch /home/ec2-user/last_build_activity

# Login to ECR
aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {ecr_registry}

# Build Docker image (ARM64 for Fargate)
docker build --platform linux/arm64 -t {image_uri} .

# Push to ECR
docker push {image_uri}

# Cleanup build directory to free disk space
rm -rf {build_dir}
touch /home/ec2-user/last_build_activity
echo "BUILD_SUCCESS"
"""
        else:
            test_tag = f"humr-test-build:{build_id}"
            build_script = f"""
set -e
cd {build_dir}
touch /home/ec2-user/last_build_activity

# Build Docker image (ARM64 for Fargate) — test only, no push
docker build --platform linux/arm64 -t {test_tag} .

# Cleanup build directory and test image
rm -rf {build_dir}
docker rmi {test_tag} || true
touch /home/ec2-user/last_build_activity
echo "BUILD_SUCCESS"
"""

        ssh_cmd = ["ssh"] + ssh_options + [f"ec2-user@{instance_id}", build_script]

        mode = "build+push" if image_uri else "test-only"
        logger.info("Running Docker build on remote instance (%(mode)s)", {"mode": mode})
        result = subprocess.run(ssh_cmd, capture_output=True, text=True)

        logs = result.stdout + result.stderr

        if result.returncode != 0 or "BUILD_SUCCESS" not in result.stdout:
            logger.error("Docker build failed (%(mode)s): %(logs)s", {"mode": mode, "logs": logs})
            return False, logs

        logger.info("Docker build completed successfully (%(mode)s)", {"mode": mode})
        return True, logs


def _build_ssh_options(key_path: Path, region: str, session: boto3.Session) -> list[str]:
    """Build SSH command options for SSM proxy connection."""
    # Get credentials from session for the proxy command
    credentials = session.get_credentials()
    frozen_credentials = credentials.get_frozen_credentials()

    # Build environment exports for the shell
    # AWS credentials only contain A-Za-z0-9+/= so no shell escaping needed
    env_exports = f"export AWS_ACCESS_KEY_ID={frozen_credentials.access_key}; "
    env_exports += f"export AWS_SECRET_ACCESS_KEY={frozen_credentials.secret_key}; "
    if frozen_credentials.token:
        env_exports += f"export AWS_SESSION_TOKEN={frozen_credentials.token}; "
    env_exports += f"export AWS_DEFAULT_REGION={region}; "

    # Use bash -c with double quotes (credentials don't contain shell-special chars)
    proxy_command = f'bash -c "{env_exports}aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p"'

    return [
        "-o", f"ProxyCommand={proxy_command}",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-i", str(key_path),
    ]
