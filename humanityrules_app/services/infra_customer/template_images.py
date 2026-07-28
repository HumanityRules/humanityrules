"""Shared, content-addressed template images.

Every template container's image is built from a tree under template_repos/
into a per-env shared ECR repo: humr/{env_slug}/<image>:<tree-hash>. The tag
is a pure function of the tree's content, so "does the tag exist in ECR?" is
the whole freshness check — the deploy path builds on miss and every app in
the env at the same tree hash shares one image. There is no version to bump
and no pre-warming; the worst case of every race here is one redundant build
of identical content, never a wrong image.
"""

import hashlib
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import boto3
from django.conf import settings
from django.db import connection

from aws_cdk import App, RemovalPolicy, Stack, Tags
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from . import cdk_utils
from . import cloudformation_utils
from . import ecr_utils

logger = logging.getLogger(__name__)

# Truncated sha256 is plenty: the tag only has to never collide across the
# handful of distinct trees a repo ever sees.
TREE_HASH_LENGTH = 16

KEEP_LAST_IMAGES = 20


class TemplateImageBuildError(RuntimeError):
    """Building or pushing a template image failed."""


def image_name(template_path: str) -> str:
    """ECR-facing image name for a template tree, e.g. 'hermes_agent' -> 'hermes-agent'."""
    return template_path.replace("_", "-")


def ecr_repo_name(env_slug: str, template_path: str) -> str:
    """Per-env shared repo for one template image: humr/{env_slug}/<image>."""
    return f"humr/{env_slug}/{image_name(template_path)}"


def ecr_stack_name(env_slug: str, template_path: str) -> str:
    """CFN stack owning one shared repo. One stack per repo — stack granularity must match lock granularity."""
    return f"humr-{env_slug}-{image_name(template_path)}-ecr"


def tree_hash(tree_path: Path) -> str:
    """Content hash of a template tree: sha256 over sorted relative paths, exec bits, and file contents.

    Hashes the tree exactly as it exists on disk — the same bytes
    transfer_source ships to the builder — so the tag honestly names the image
    content wherever it's computed (prod container or dev working tree).
    """
    digest = hashlib.sha256()
    entries = sorted(tree_path.rglob("*"), key=lambda p: p.relative_to(tree_path).as_posix())
    for path in entries:
        rel = path.relative_to(tree_path).as_posix()
        # Symlinks first: is_file() follows links and chokes on dangling ones
        # (e.g. a dev .venv pointing at a non-existent interpreter).
        if path.is_symlink():
            digest.update(f"L {rel} -> {path.readlink().as_posix()}\n".encode())
        elif path.is_file():
            exec_bit = "x" if path.stat().st_mode & 0o100 else "-"
            digest.update(f"F {exec_bit} {rel}\n".encode())
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()[:TREE_HASH_LENGTH]


@contextmanager
def _repo_build_lock(account_id: str, repo_name: str) -> Iterator[None]:
    """Blocking Postgres advisory lock serializing the build slow path per (account, repo).

    Session-scoped: a crashed holder releases on disconnect and the waiter
    re-checks ECR, so there is no stale-lock janitor. On SQLite (single-worker
    local dev) there is nothing to lock against — no-op.
    """
    if connection.vendor != "postgresql":
        yield
        return
    key = int.from_bytes(hashlib.sha256(f"{account_id}/{repo_name}".encode()).digest()[:8], byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [key])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


class TemplateImageEcrStack(Stack):
    """Per-env shared ECR repo for one template image. Deployed only under the repo's build lock."""

    def __init__(self, scope: Construct, construct_id: str, env_slug: str, template_path: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = image_name(template_path)
        # The policy-proxy repo predates this stack class under the same stack
        # name; keeping its original logical id makes the first deploy a no-op
        # update instead of a replace (which would collide on the repo name).
        repo_construct_id = "PolicyProxyEcrRepository" if name == "policy-proxy" else "TemplateImageEcrRepository"
        self.repository = ecr.Repository(
            self, repo_construct_id,
            repository_name=ecr_repo_name(env_slug=env_slug, template_path=template_path),
            image_scan_on_push=True,
            lifecycle_rules=[ecr.LifecycleRule(description=f"Keep last {KEEP_LAST_IMAGES} images", max_image_count=KEEP_LAST_IMAGES, rule_priority=1)],
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )
        Tags.of(self.repository).add("Env", env_slug)
        Tags.of(self.repository).add("TemplateImage", name)


def _deploy_ecr_stack(session: boto3.Session, env_slug: str, template_path: str) -> None:
    """Synth + deploy the shared repo's stack. A no-op changeset when nothing changed."""
    stack_name = ecr_stack_name(env_slug=env_slug, template_path=template_path)
    cloudformation_utils.cleanup_rollback_complete_stacks(session.client("cloudformation"), [stack_name])

    with cdk_utils.jsii_synth_lock:
        cdk_app = App(outdir=str(cdk_utils.create_synth_dir(name=stack_name)))
        TemplateImageEcrStack(cdk_app, stack_name, env_slug=env_slug, template_path=template_path)
        assembly_dir = cdk_utils.synth_cdk_app(cdk_app)

    if not cdk_utils.deploy_from_assembly(assembly_dir=assembly_dir, session=session, stack_names=[stack_name]):
        raise TemplateImageBuildError(f"ECR stack deployment failed: {stack_name}")


def ensure_template_image(session: boto3.Session, account_id: str, region: str, env_slug: str, template_path: str, build_id: str) -> str:
    """Return the tree-hash tag for *template_path*, building and pushing the image if it's absent from ECR.

    Flow: check tag → blocking per-(account, repo) advisory lock → re-check
    (the loser of a race arrives after the winner pushed) → deploy the repo's
    CFN stack → build + push → unlock. The fast path touches neither CFN nor
    the lock: an existing tag implies an existing repo.
    """
    tree_path = settings.TEMPLATE_REPOS_DIR / template_path
    if not tree_path.is_dir():
        raise TemplateImageBuildError(f"Template tree does not exist: {tree_path}")

    tag = tree_hash(tree_path)
    repo_name = ecr_repo_name(env_slug=env_slug, template_path=template_path)

    if ecr_utils.image_tag_exists(session=session, ecr_repo_name=repo_name, image_tag=tag):
        logger.info("Template image %(repo)s:%(tag)s already in ECR", {"repo": repo_name, "tag": tag})
        return tag

    with _repo_build_lock(account_id=account_id, repo_name=repo_name):
        if ecr_utils.image_tag_exists(session=session, ecr_repo_name=repo_name, image_tag=tag):
            logger.info("Template image %(repo)s:%(tag)s built by a concurrent deploy", {"repo": repo_name, "tag": tag})
            return tag

        logger.info("Building template image %(repo)s:%(tag)s", {"repo": repo_name, "tag": tag})
        _deploy_ecr_stack(session=session, env_slug=env_slug, template_path=template_path)

        image_uri = ecr_utils.build_and_push_docker_image(
            session=session,
            account_id=account_id,
            region=region,
            env_slug=env_slug,
            app_name=image_name(template_path),
            ecr_repo_name=repo_name,
            app_source_path=tree_path,
            image_tag=tag,
            build_id=build_id,
        )
        if not image_uri:
            raise TemplateImageBuildError(f"Build/push failed for template image {repo_name}:{tag}")

    return tag
