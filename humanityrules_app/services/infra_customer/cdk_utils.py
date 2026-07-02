"""
CDK utility functions for deploying stacks.
"""

import logging
import os
from pathlib import Path
import subprocess

import boto3
from aws_cdk import App

CDK_OUT_DIR = Path(__file__).parent / "cdk.out"

logger = logging.getLogger(__name__)


def _cdk_level_for_line(line: str) -> int:
    failure_tokens = (
        "FAILED",
        "ROLLBACK",
        "ERROR",
        "CANCELLED",
    )
    upper_line = line.upper()
    if any(token in upper_line for token in failure_tokens):
        return logging.ERROR
    return logging.INFO


def _stream_cdk_output(stream) -> None:
    """Stream CDK output, detecting error lines by content."""
    if stream is None:
        return
    for line in stream:
        cleaned = line.rstrip("\n")
        if not cleaned:
            continue
        level = _cdk_level_for_line(cleaned)
        logger.log(level, "%(line)s", {"line": cleaned}, extra={"source": "cdk"})


def synth_cdk_app(app: App) -> str:
    """Synthesize CDK app and return the cloud assembly directory path."""
    cloud_assembly = app.synth()
    logger.info("Synthesized CDK stacks to: %(directory)s", {"directory": cloud_assembly.directory})
    return cloud_assembly.directory


def _get_cdk_env(session: boto3.Session) -> dict[str, str]:
    """Build environment dict with AWS credentials for the CDK CLI subprocess."""
    credentials = session.get_credentials()
    frozen_credentials = credentials.get_frozen_credentials()

    cdk_env = os.environ.copy()
    cdk_env["AWS_ACCESS_KEY_ID"] = frozen_credentials.access_key
    cdk_env["AWS_SECRET_ACCESS_KEY"] = frozen_credentials.secret_key
    if frozen_credentials.token:
        cdk_env["AWS_SESSION_TOKEN"] = frozen_credentials.token
    return cdk_env


def deploy_from_assembly(assembly_dir: str, session: boto3.Session, stack_names: list[str] | None) -> bool:
    """Deploy CDK stacks from a pre-synthesized cloud assembly directory."""
    if stack_names:
        target_args = stack_names
        logger.info("Deploying CDK stacks: %(stacks)s", {"stacks": ", ".join(stack_names)})
    else:
        target_args = ["--all"]
        logger.info("Deploying all CDK stacks")

    cdk_env = _get_cdk_env(session)

    cmd = ["npx", "--yes", "cdk", "deploy"] + target_args + [
        "--ci", "--progress", "events", "--require-approval", "never", "--no-notices", "--express", "--rollback", "--app", assembly_dir,
    ]

    process = subprocess.Popen(
        cmd,
        env=cdk_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Merge stderr into stdout (--ci sends logs to stdout anyway)
        text=True,
        bufsize=1,
    )
    _stream_cdk_output(process.stdout)
    process.wait()

    if process.returncode != 0:
        logger.error("CDK deployment failed")
        return False

    logger.info("CDK deployment complete")
    return True


def deploy_cdk_stacks(app: App, session: boto3.Session) -> bool:
    """Synthesize and deploy all CDK stacks in one shot."""
    assembly_dir = synth_cdk_app(app)
    return deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=None)
