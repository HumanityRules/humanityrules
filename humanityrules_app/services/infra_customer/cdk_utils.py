"""
CDK utility functions for deploying stacks.
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from uuid import uuid4

import boto3
from aws_cdk import App

from . import cloudformation_utils

CDK_OUT_DIR = Path(__file__).parent / "cdk.out"

# Deployments run in parallel worker threads. Two of them synthesizing into
# the same assembly directory overwrite each other's manifest, and the slower
# deploy then fails its `cdk deploy` with "No stacks match the name(s) ...".
# Every synth therefore gets its own subdirectory; stale ones are pruned on
# the next synth instead of a try/finally so an operator can still inspect
# the assembly of a just-finished (or failed) deployment.
_STALE_SYNTH_DIR_SECONDS = 24 * 3600

# Every CDK construct call and synth in this process round-trips through one
# shared jsii kernel (a node subprocess) whose stdio protocol has no framing
# per caller: concurrent threads consume each other's responses and fail with
# garbage like "argument of type 'ObjRef' is not a container or iterable".
# Hold this lock from App() construction through synth_cdk_app(). Deploys read
# the assembly from disk in their own subprocess and must run outside it.
jsii_synth_lock = threading.Lock()

logger = logging.getLogger(__name__)


def create_synth_dir(name: str) -> Path:
    """Create a fresh per-invocation synth dir under CDK_OUT_DIR and prune stale siblings."""
    _prune_stale_synth_dirs()
    synth_dir = CDK_OUT_DIR / f"{name}-{uuid4().hex[:8]}"
    synth_dir.mkdir(parents=True, exist_ok=False)
    return synth_dir


def _prune_stale_synth_dirs() -> None:
    """Best-effort removal of synth dirs older than _STALE_SYNTH_DIR_SECONDS."""
    if not CDK_OUT_DIR.exists():
        return
    cutoff = time.time() - _STALE_SYNTH_DIR_SECONDS
    for entry in CDK_OUT_DIR.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


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

    # --rollback must accompany --express: express turns rollback off by default, and headless CDK
    # refuses replacement updates (every task-definition change) with rollback disabled.
    express_args = ["--express", "--rollback"] if cloudformation_utils.use_express_mode() else []
    cmd = ["npx", "--yes", "cdk", "deploy"] + target_args + express_args + [
        "--ci", "--progress", "events", "--require-approval", "never", "--no-notices", "--app", assembly_dir,
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
