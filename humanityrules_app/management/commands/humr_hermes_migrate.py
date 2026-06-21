"""
Migrate a hermes_agent app's persistent-root state from a source env to a dest env.

The five-phase pattern (each phase is idempotent and can be rerun on its own):

  1. upload      Tar /hermes-persistent-root inside the source container, pipe
                 through `aws s3 cp -` into a bridge S3 bucket. Captures sha256
                 + raw size + compressed size on the SSM control channel.
  2. stage       Run a one-shot Fargate task in the dest env that mounts the
                 dest EFS root, downloads the object, verifies sha + size,
                 atomic-mv's into /efs/deployments/<app>/checkpoint/rootfs.tar.zst.
                 The dest hermes container must be stopped (`--stop-dest`)
                 before this — otherwise its persistent-root-runner.sh will
                 overwrite our staged tar on SIGTERM.
  3. host-clear  rm -rf /var/lib/humr/hermes-roots/<app> on the dest EC2
                 instance, so the runner sees an empty persistent root and
                 restores from the staged checkpoint.
  4. finalize    Scale the dest ECS service back to 1 and wait for the runner's
                 "Restore complete" log line.
  5. verify      ECS-exec into the freshly restarted dest container and
                 measure /hermes-persistent-root/{workspace,home/linuxbrew}.
  cleanup       (Separate phase, opt-in.) Empty + delete the bridge S3 bucket
                 and the temp stager IAM role.

Usage (same-account, the common case):
    uv run manage.py humr_hermes_migrate \\
        --account "CH Sandbox" --source-env default --dest-env sandbox \\
        --app hermes-vmendi00

Usage (cross-account):
    uv run manage.py humr_hermes_migrate \\
        --source-account "CH Sandbox" --source-env default \\
        --dest-account "Other AWS"  --dest-env prod \\
        --app hermes-vmendi00

Raw mode (when local DB doesn't know one of the envs — e.g. dest is
prod-controlplane managed):
    uv run manage.py humr_hermes_migrate \\
        --source-account "CH Sandbox" --source-env default \\
        --dest-aws-account-id 266117665083 \\
        --dest-aws-external-id 6484b2c0-c50e-4f3e-ba0f-5252f129ef8d \\
        --dest-aws-region us-east-1 \\
        --dest-env-slug sandbox \\
        --app hermes-vmendi00

Phases (default is the full chain except cleanup):
    --phases upload,stage,host-clear,finalize,verify
    --phases stage          # rerun a single phase
    --phases cleanup        # tear down bridge bucket + stager role

Safety notes:
    - This is destructive: dest's existing /hermes-persistent-root and EFS
      checkpoint are *replaced*. The command refuses to run without
      `--i-know-this-overwrites-dest`.
    - The dest ECS service is scaled to 0 in phase `stage` and back to its
      previous desired count in `finalize`. If you rerun phases out of order
      you may leave dest scaled to 0.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.services.infra_customer import cloudformation_utils

from ._aws_account_resolver import (
    ResolvedAwsTarget,
    _build_session,
    get_aws_account,
)
from humanityrules_app.models import Environment


PHASES = ("upload", "stage", "host-clear", "finalize", "verify", "cleanup")
DEFAULT_PHASES = ("upload", "stage", "host-clear", "finalize", "verify")
HERMES_CONTAINER_SUFFIX = "-hermes"
CHECKPOINT_KEY = "rootfs.tar.zst"
STAGER_IMAGE = "public.ecr.aws/aws-cli/aws-cli:latest"

# tar exclusions matching persistent-root-runner.sh + nested mount-points
TAR_EXCLUDES = (
    './dev', './proc', './run', './sys', './tmp',
    './opt/doh', './opt/hermes',
    './hermes-persistent-root', './hermes-checkpoint',
)


@dataclass
class MigrationContext:
    app: str
    source: ResolvedAwsTarget
    dest: ResolvedAwsTarget
    bridge_bucket: str
    stop_dest: bool
    state_file: str
    overwrite_acked: bool


class Command(BaseCommand):
    help = "Migrate a hermes_agent app's persistent root from a source env to a dest env."

    def add_arguments(self, parser):
        parser.add_argument("--app", required=True, help="App slug (same on source and dest)")

        # DB mode shortcut: shared --account when source and dest are in the same AWS account.
        parser.add_argument("--account", help="Shared AWS account name/ID for both source and dest (DB mode shortcut)")
        parser.add_argument("--org", help="Organization name/slug (DB mode disambiguation)")
        parser.add_argument("--source-env", help="Source environment slug (DB mode)")
        parser.add_argument("--dest-env", help="Dest environment slug (DB mode)")

        # Per-side DB mode (cross-account).
        parser.add_argument("--source-account", help="Source AWS account name/ID (DB mode)")
        parser.add_argument("--dest-account", help="Dest AWS account name/ID (DB mode)")

        # Per-side raw mode (when local DB doesn't know the env).
        for side in ("source", "dest"):
            parser.add_argument(f"--{side}-aws-account-id", dest=f"{side}_aws_account_id")
            parser.add_argument(f"--{side}-aws-external-id", dest=f"{side}_aws_external_id")
            parser.add_argument(f"--{side}-aws-region", dest=f"{side}_aws_region")
            parser.add_argument(f"--{side}-env-slug", dest=f"{side}_env_slug")

        parser.add_argument("--phases", default=",".join(DEFAULT_PHASES),
            help=f"Comma-separated subset of {','.join(PHASES)}. Default: {','.join(DEFAULT_PHASES)}")
        parser.add_argument("--bridge-bucket",
            help="Existing S3 bucket to use as bridge. If omitted, a temporary bucket is created in the source account with a 7-day lifecycle.")
        parser.add_argument("--state-file", default=None,
            help="Path to a JSON file used to persist {bucket, sha, size, prev_desired} across phases. "
                 "Defaults to /tmp/hermes-migrate-<app>.json.")
        parser.add_argument("--stop-dest", action="store_true",
            help="During `stage`, scale the dest service to 0 before staging and remember the previous "
                 "desiredCount for `finalize`. Required for the runner not to clobber the staged tar.")
        parser.add_argument("--i-know-this-overwrites-dest", action="store_true",
            help="Acknowledge that dest's persistent root and checkpoint will be replaced.")

    def handle(self, *args, **options):
        ctx = self._resolve_context(options=options)
        phases = [p.strip() for p in options["phases"].split(",") if p.strip()]
        for p in phases:
            if p not in PHASES:
                raise CommandError(f"Unknown phase {p!r}; valid: {','.join(PHASES)}")
        if any(p in phases for p in ("stage", "host-clear", "finalize")) and not ctx.overwrite_acked:
            raise CommandError(
                "Phases stage / host-clear / finalize will replace dest state. "
                "Re-run with --i-know-this-overwrites-dest."
            )

        for p in phases:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== phase: {p} ==="))
            getattr(self, f"_phase_{p.replace('-', '_')}")(ctx=ctx)

    # -- context resolution -------------------------------------------------

    def _resolve_context(self, options: dict) -> MigrationContext:
        source = self._resolve_side(side="source", options=options)
        dest = self._resolve_side(side="dest", options=options)
        if source.aws_account_id == dest.aws_account_id and source.env_slug == dest.env_slug:
            raise CommandError("Source and dest are the same env. Nothing to migrate.")

        bucket = options.get("bridge_bucket")
        if not bucket:
            # Will be created during phase `upload` if absent; default name embeds the app slug + a uuid suffix.
            bucket = f"hermes-{options['app']}-migration-{int(time.time())}-{uuid.uuid4().hex[:6]}"

        state_file = options.get("state_file") or f"/tmp/hermes-migrate-{options['app']}.json"

        return MigrationContext(
            app=options["app"],
            source=source,
            dest=dest,
            bridge_bucket=bucket,
            stop_dest=options.get("stop_dest", False),
            state_file=state_file,
            overwrite_acked=options.get("i_know_this_overwrites_dest", False),
        )

    def _resolve_side(self, side: str, options: dict) -> ResolvedAwsTarget:
        """Resolve --source-* / --dest-* into a ResolvedAwsTarget. Two-arg DB shortcut: --account + --<side>-env."""
        raw_keys = (f"{side}_aws_account_id", f"{side}_aws_external_id", f"{side}_aws_region", f"{side}_env_slug")
        raw = {k: options.get(k) for k in raw_keys}
        any_raw = any(raw.values())

        if any_raw:
            missing = [k for k, v in raw.items() if not v]
            if missing:
                raise CommandError(f"Raw mode for {side} requires all of: {', '.join('--'+k.replace('_','-') for k in missing)}")
            return self._build_target(
                aws_account_id=raw[f"{side}_aws_account_id"],
                external_id=raw[f"{side}_aws_external_id"],
                region=raw[f"{side}_aws_region"],
                env_slug=raw[f"{side}_env_slug"],
            )

        account = options.get(f"{side}_account") or options.get("account")
        env = options.get(f"{side}_env")
        if not account or not env:
            raise CommandError(
                f"Missing {side} target. Provide either --{side}-account/--{side}-env, "
                f"or --account + --{side}-env, or all four --{side}-aws-* raw flags."
            )
        return self._resolve_db(account=account, org=options.get("org"), env_slug=env)

    def _resolve_db(self, account: str, org: str | None, env_slug: str) -> ResolvedAwsTarget:
        aws_account = get_aws_account(identifier=account, org_slug=org)
        try:
            environment = Environment.objects.get(aws_account=aws_account, slug=env_slug)
        except Environment.DoesNotExist:
            raise CommandError(f"No env with slug '{env_slug}' for account '{aws_account.name}'.")
        return self._build_target(
            aws_account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=environment.aws_region,
            env_slug=environment.slug,
            aws_account=aws_account,
            environment=environment,
        )

    def _build_target(self, aws_account_id, external_id, region, env_slug, aws_account=None, environment=None):
        session = _build_session(aws_account_id=aws_account_id, external_id=external_id, region=region)
        return ResolvedAwsTarget(
            aws_account_id=aws_account_id,
            external_id=external_id,
            aws_region=region,
            env_slug=env_slug,
            session=session,
            aws_account=aws_account,
            environment=environment,
        )

    # -- state ----------------------------------------------------------------

    def _read_state(self, ctx: MigrationContext) -> dict:
        if not os.path.exists(ctx.state_file):
            return {}
        with open(ctx.state_file) as f:
            return json.load(f)

    def _write_state(self, ctx: MigrationContext, state: dict) -> None:
        with open(ctx.state_file, "w") as f:
            json.dump(state, f, indent=2)
        self.stdout.write(f"  state -> {ctx.state_file}")

    # -- phase 1: upload ------------------------------------------------------

    def _phase_upload(self, ctx: MigrationContext):
        state = self._read_state(ctx=ctx)
        bucket = state.get("bucket") or ctx.bridge_bucket
        cross_account = ctx.source.aws_account_id != ctx.dest.aws_account_id

        s3_src = ctx.source.session.client("s3")
        self._ensure_bridge_bucket(s3=s3_src, bucket=bucket, region=ctx.source.aws_region,
            cross_account=cross_account, dest_account_id=ctx.dest.aws_account_id)

        # Find source running task.
        ecs = ctx.source.session.client("ecs")
        cluster = f"humr-{ctx.source.env_slug}-cluster"
        service = f"doh-{ctx.source.env_slug}-{ctx.app}"
        container = f"{ctx.app}{HERMES_CONTAINER_SUFFIX}"
        task = self._find_running_task(ecs=ecs, cluster=cluster, service=service)
        self.stdout.write(f"  source task: {task}")

        # STS creds for the source role get forwarded into the inner shell so
        # `aws s3 cp` works regardless of what the task role allows.
        creds = ctx.source.session.get_credentials().get_frozen_credentials()
        excludes = " ".join(f"--exclude={shlex.quote(e)}" for e in TAR_EXCLUDES)
        inner = (
            "set -e; "
            f"export AWS_ACCESS_KEY_ID={shlex.quote(creds.access_key)}; "
            f"export AWS_SECRET_ACCESS_KEY={shlex.quote(creds.secret_key)}; "
            f"export AWS_SESSION_TOKEN={shlex.quote(creds.token)}; "
            f"export AWS_DEFAULT_REGION={ctx.source.aws_region}; "
            "echo BEGIN_$(date -u +%H:%M:%S); "
            "cd /hermes-persistent-root; "
            f"tar --create --zstd --one-file-system --numeric-owner --xattrs --acls {excludes} . "
            "| tee >(sha256sum > /tmp/.hash.txt) >(wc -c > /tmp/.size.txt) "
            f"| aws s3 cp - s3://{bucket}/{CHECKPOINT_KEY} --no-progress; "
            "echo END_$(date -u +%H:%M:%S); "
            "echo SHA=$(awk '{print $1}' /tmp/.hash.txt); "
            "echo RAW_SIZE=$(cat /tmp/.size.txt); "
            f"echo COMP_SIZE=$(aws s3api head-object --bucket {bucket} --key {CHECKPOINT_KEY} --query ContentLength --output text); "
            "rm -f /tmp/.hash.txt /tmp/.size.txt"
        )

        out = self._ssm_exec_capture(target=ctx.source, cluster=cluster, task=task,
            container=container, inner=inner, timeout=14400)

        sha, raw_size, comp_size = self._parse_upload_trailer(stdout=out)
        self.stdout.write(self.style.SUCCESS(
            f"  uploaded: sha={sha} raw={raw_size} comp={comp_size}"))
        state.update({"bucket": bucket, "sha": sha, "raw_size": raw_size, "comp_size": comp_size})
        self._write_state(ctx=ctx, state=state)

    # -- phase 2: stage -------------------------------------------------------

    def _phase_stage(self, ctx: MigrationContext):
        state = self._read_state(ctx=ctx)
        if not (state.get("bucket") and state.get("sha") and state.get("comp_size")):
            raise CommandError("State file missing bucket/sha/comp_size — run `upload` first.")

        ecs = ctx.dest.session.client("ecs")
        cluster = f"humr-{ctx.dest.env_slug}-cluster"
        service = f"doh-{ctx.dest.env_slug}-{ctx.app}"

        if ctx.stop_dest:
            svc = ecs.describe_services(cluster=cluster, services=[service])["services"][0]
            prev_desired = svc["desiredCount"]
            self.stdout.write(f"  dest service desiredCount={prev_desired}, scaling to 0")
            ecs.update_service(cluster=cluster, service=service, desiredCount=0)
            self._wait_for_no_running_tasks(ecs=ecs, cluster=cluster, service=service)
            state["prev_desired"] = prev_desired
            self._write_state(ctx=ctx, state=state)

        # Run the Fargate stager.
        self._run_stager_task(ctx=ctx, bucket=state["bucket"], sha=state["sha"], size=int(state["comp_size"]))

    # -- phase 3: host-clear -------------------------------------------------

    def _phase_host_clear(self, ctx: MigrationContext):
        ssm = ctx.dest.session.client("ssm")
        ecs = ctx.dest.session.client("ecs")
        cluster = f"humr-{ctx.dest.env_slug}-cluster"
        ci_arns = ecs.list_container_instances(cluster=cluster)["containerInstanceArns"]
        if not ci_arns:
            raise CommandError("No container instances in dest cluster.")
        cis = ecs.describe_container_instances(cluster=cluster, containerInstances=ci_arns)["containerInstances"]
        instance_ids = [c["ec2InstanceId"] for c in cis if c["status"] == "ACTIVE"]
        if not instance_ids:
            raise CommandError("No ACTIVE container instances in dest cluster.")
        target_dir = f"/var/lib/humr/hermes-roots/{ctx.app}"
        script = f"set -e; echo === before ===; ls -la {target_dir} 2>/dev/null || echo MISSING; rm -rf {target_dir}; mkdir -p {target_dir}; echo === after ===; ls -la {target_dir}"

        for inst in instance_ids:
            self.stdout.write(f"  host-clear on {inst}...")
            r = ssm.send_command(InstanceIds=[inst], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [script]}, TimeoutSeconds=120)
            cid = r["Command"]["CommandId"]
            for _ in range(60):
                time.sleep(2)
                inv = ssm.get_command_invocation(CommandId=cid, InstanceId=inst)
                if inv["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                    if inv["Status"] != "Success":
                        raise CommandError(f"host-clear failed on {inst}: {inv['Status']}\n{inv['StandardErrorContent']}")
                    self.stdout.write(inv["StandardOutputContent"])
                    break
            else:
                raise CommandError(f"host-clear timed out on {inst}")

    # -- phase 4: finalize ---------------------------------------------------

    def _phase_finalize(self, ctx: MigrationContext):
        state = self._read_state(ctx=ctx)
        prev = state.get("prev_desired", 1)
        ecs = ctx.dest.session.client("ecs")
        cluster = f"humr-{ctx.dest.env_slug}-cluster"
        service = f"doh-{ctx.dest.env_slug}-{ctx.app}"
        self.stdout.write(f"  scaling dest service to desiredCount={prev}")
        ecs.update_service(cluster=cluster, service=service, desiredCount=prev)
        self._wait_for_restore_complete(ctx=ctx, cluster=cluster, service=service)

    # -- phase 5: verify -----------------------------------------------------

    def _phase_verify(self, ctx: MigrationContext):
        ecs = ctx.dest.session.client("ecs")
        cluster = f"humr-{ctx.dest.env_slug}-cluster"
        service = f"doh-{ctx.dest.env_slug}-{ctx.app}"
        container = f"{ctx.app}{HERMES_CONTAINER_SUFFIX}"
        task = self._find_running_task(ecs=ecs, cluster=cluster, service=service)
        inner = (
            "echo === workspace ===; du -sh /hermes-persistent-root/workspace 2>/dev/null || true; "
            "echo === linuxbrew ===; du -sh /hermes-persistent-root/home/linuxbrew 2>/dev/null || true; "
            "echo === df ===; df -h /hermes-persistent-root; "
            "echo === checkpoint ===; ls -la /hermes-checkpoint/"
        )
        out = self._ssm_exec_capture(target=ctx.dest, cluster=cluster, task=task,
            container=container, inner=inner, timeout=180)
        self.stdout.write(out)

    # -- phase: cleanup ------------------------------------------------------

    def _phase_cleanup(self, ctx: MigrationContext):
        state = self._read_state(ctx=ctx)
        bucket = state.get("bucket") or ctx.bridge_bucket
        s3 = ctx.source.session.client("s3")
        try:
            self._empty_bucket(s3=s3, bucket=bucket)
            s3.delete_bucket(Bucket=bucket)
            self.stdout.write(f"  deleted bucket {bucket}")
        except ClientError as e:
            self.stdout.write(self.style.WARNING(f"  s3 cleanup error (continuing): {e}"))

        # Stager role lives in dest account.
        iam = ctx.dest.session.client("iam")
        role_name = f"humr-{ctx.dest.env_slug}-checkpoint-stager-role"
        try:
            for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            self.stdout.write(f"  deleted role {role_name}")
        except ClientError as e:
            self.stdout.write(self.style.WARNING(f"  iam cleanup error (continuing): {e}"))

        if os.path.exists(ctx.state_file):
            os.remove(ctx.state_file)
            self.stdout.write(f"  removed state file {ctx.state_file}")

    # -- helpers: bucket -----------------------------------------------------

    def _ensure_bridge_bucket(self, s3, bucket: str, region: str, cross_account: bool, dest_account_id: str):
        try:
            s3.head_bucket(Bucket=bucket)
            self.stdout.write(f"  bridge bucket exists: {bucket}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in ("404", "NoSuchBucket"):
                raise
            self.stdout.write(f"  creating bridge bucket: {bucket}")
            kwargs = {"Bucket": bucket}
            if region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
            s3.create_bucket(**kwargs)
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={"Rules": [{
                    "ID": "expire-7d", "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": 7},
                }]},
            )
            s3.put_public_access_block(
                Bucket=bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True, "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
                },
            )

        if cross_account:
            self.stdout.write(f"  attaching cross-account read policy for {dest_account_id}")
            policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Sid": "AllowDestRoleRead",
                    "Effect": "Allow",
                    "Principal": {"AWS": f"arn:aws:iam::{dest_account_id}:root"},
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                }],
            }
            s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))

    def _empty_bucket(self, s3, bucket: str):
        # Versions + delete markers, then current objects.
        for paginator_name, key in (("list_object_versions", ("Versions", "DeleteMarkers")),
                                    ("list_objects_v2", ("Contents",))):
            paginator = s3.get_paginator(paginator_name)
            batch = []
            for page in paginator.paginate(Bucket=bucket):
                for k in key:
                    for obj in page.get(k, []) or []:
                        item = {"Key": obj["Key"]}
                        if "VersionId" in obj:
                            item["VersionId"] = obj["VersionId"]
                        batch.append(item)
                        if len(batch) == 1000:
                            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})
                            batch = []
            if batch:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})

    # -- helpers: stager -----------------------------------------------------

    def _run_stager_task(self, ctx: MigrationContext, bucket: str, sha: str, size: int):
        sess = ctx.dest.session
        ecs = sess.client("ecs")
        iam = sess.client("iam")
        logs = sess.client("logs")
        cf = sess.client("cloudformation")

        env_slug = ctx.dest.env_slug
        cluster = f"humr-{env_slug}-cluster"
        log_group = f"/humr/{env_slug}/ecs"
        exec_role = f"arn:aws:iam::{ctx.dest.aws_account_id}:role/humr-{env_slug}-task-execution-role"
        family = f"humr-{env_slug}-checkpoint-stager"
        role_name = f"humr-{env_slug}-checkpoint-stager-role"

        infra = self._lookup_infra(cf=cf, env_slug=env_slug)
        efs_arn = f"arn:aws:elasticfilesystem:{ctx.dest.aws_region}:{ctx.dest.aws_account_id}:file-system/{infra['efs_fs_id']}"

        bucket_arn = f"arn:aws:s3:::{bucket}"
        role_arn = self._ensure_stager_role(iam=iam, role_name=role_name, efs_arn=efs_arn, bucket_arn=bucket_arn)
        self.stdout.write("  waiting 10s for IAM propagation...")
        time.sleep(10)

        target_dir = f"/efs/deployments/{ctx.app}/checkpoint"
        target = f"{target_dir}/rootfs.tar.zst"
        staging = f"{target}.staging"
        cmd = (
            "set -e; "
            f"mkdir -p {target_dir}; "
            f"echo === before ===; ls -lah {target_dir} || true; "
            f"echo === remove orphans ===; rm -f {target_dir}/rootfs.tar.zst.tmp.* {staging}; "
            f"echo === download ===; aws s3 cp s3://{bucket}/{CHECKPOINT_KEY} {staging} --no-progress; "
            "echo === verify ===; "
            f"actual_sha=$(sha256sum {staging} | awk '{{print $1}}'); "
            f"actual_sz=$(stat -c %s {staging}); "
            "echo SHA=$actual_sha; echo SZ=$actual_sz; "
            f"if [ \"$actual_sha\" != \"{sha}\" ]; then echo SHA_MISMATCH; exit 2; fi; "
            f"if [ \"$actual_sz\" != \"{size}\" ]; then echo SIZE_MISMATCH; exit 3; fi; "
            f"echo === atomic mv ===; mv -f {staging} {target}; "
            f"echo === after ===; ls -lah {target_dir}; "
            "echo DONE"
        )
        td = ecs.register_task_definition(
            family=family,
            taskRoleArn=role_arn,
            executionRoleArn=exec_role,
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu="1024", memory="2048",
            runtimePlatform={"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
            volumes=[{
                "name": "efs-root",
                "efsVolumeConfiguration": {
                    "fileSystemId": infra["efs_fs_id"],
                    "transitEncryption": "ENABLED",
                    "authorizationConfig": {"iam": "ENABLED"},
                },
            }],
            containerDefinitions=[{
                "name": "stager",
                "image": STAGER_IMAGE,
                "essential": True,
                "entryPoint": ["/bin/sh", "-c"],
                "command": [cmd],
                "mountPoints": [{"containerPath": "/efs", "sourceVolume": "efs-root", "readOnly": False}],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": log_group,
                        "awslogs-region": ctx.dest.aws_region,
                        "awslogs-stream-prefix": "stager",
                    },
                },
                "linuxParameters": {"initProcessEnabled": True},
            }],
        )
        td_arn = td["taskDefinition"]["taskDefinitionArn"]

        try:
            run = ecs.run_task(cluster=cluster, taskDefinition=td_arn, launchType="FARGATE",
                platformVersion="LATEST",
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": [infra["subnet_1"], infra["subnet_2"]],
                    "securityGroups": [infra["default_sg"], infra["efs_sg"]],
                    "assignPublicIp": "DISABLED",
                }})
            failures = run.get("failures", [])
            if failures:
                raise CommandError(f"run_task failed: {failures}")
            task_arn = run["tasks"][0]["taskArn"]
            self.stdout.write(f"  stager task: {task_arn.split('/')[-1]}")
            self._wait_stopped(ecs=ecs, cluster=cluster, task_arn=task_arn)
            self._dump_log_stream(logs=logs, log_group=log_group,
                stream=f"stager/stager/{task_arn.split('/')[-1]}")
            r = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
            ec = r["containers"][0].get("exitCode")
            if ec != 0:
                raise CommandError(f"stager exited with code {ec}")
        finally:
            try:
                ecs.deregister_task_definition(taskDefinition=td_arn)
                ecs.delete_task_definitions(taskDefinitions=[td_arn])
            except ClientError:
                pass

    def _lookup_infra(self, cf, env_slug: str) -> dict:
        vpc_stack = f"humr-{env_slug}-vpc"
        efs_stack = f"humr-{env_slug}-efs"
        keys = {
            "efs_fs_id": (efs_stack, "EfsFileSystemId"),
            "efs_sg":    (efs_stack, "EfsSecurityGroupId"),
            "subnet_1":  (vpc_stack, "PrivateSubnet1Id"),
            "subnet_2":  (vpc_stack, "PrivateSubnet2Id"),
            "default_sg": (vpc_stack, "DefaultSecurityGroupId"),
        }
        out = {}
        for k, (stack, name) in keys.items():
            v = cloudformation_utils.get_stack_output(cf, stack, name)
            if not v:
                raise CommandError(f"Missing output {name} from stack {stack}")
            out[k] = v
        return out

    def _ensure_stager_role(self, iam, role_name: str, efs_arn: str, bucket_arn: str) -> str:
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })
        try:
            r = iam.get_role(RoleName=role_name)
            arn = r["Role"]["Arn"]
        except iam.exceptions.NoSuchEntityException:
            r = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=trust,
                Description="One-shot stager: download S3 -> EFS for hermes migration",
                Tags=[{"Key": "humr:purpose", "Value": "checkpoint-stager"}])
            arn = r["Role"]["Arn"]
        iam.put_role_policy(RoleName=role_name, PolicyName="ssm",
            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow",
                "Action": ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
                           "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"],
                "Resource": "*",
            }]}))
        iam.put_role_policy(RoleName=role_name, PolicyName="efs",
            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow",
                "Action": ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite",
                           "elasticfilesystem:ClientRootAccess"],
                "Resource": efs_arn,
            }]}))
        iam.put_role_policy(RoleName=role_name, PolicyName="s3-bridge",
            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"{bucket_arn}/*"},
                {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": bucket_arn},
            ]}))
        return arn

    # -- helpers: misc -------------------------------------------------------

    def _find_running_task(self, ecs, cluster: str, service: str) -> str:
        arns = ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING")["taskArns"]
        if not arns:
            raise CommandError(f"No RUNNING task in service {service} (cluster {cluster}).")
        return arns[0].split("/")[-1]

    def _wait_for_no_running_tasks(self, ecs, cluster: str, service: str, timeout: int = 300):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            arns = ecs.list_tasks(cluster=cluster, serviceName=service)["taskArns"]
            if not arns:
                self.stdout.write("  dest tasks drained")
                return
            time.sleep(5)
        raise CommandError("dest service did not drain in time")

    def _wait_stopped(self, ecs, cluster: str, task_arn: str, timeout: int = 1800):
        start = time.monotonic()
        last = None
        while time.monotonic() - start < timeout:
            r = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
            st = r["lastStatus"]
            if st != last:
                self.stdout.write(f"  [{int(time.monotonic()-start)}s] {st}")
                last = st
            if st == "STOPPED":
                return
            time.sleep(10)
        raise CommandError("stager did not stop in time")

    def _wait_for_restore_complete(self, ctx: MigrationContext, cluster: str, service: str, timeout: int = 900):
        logs = ctx.dest.session.client("logs")
        ecs = ctx.dest.session.client("ecs")
        log_group = f"/humr/{ctx.dest.env_slug}/ecs"
        container = f"{ctx.app}{HERMES_CONTAINER_SUFFIX}"
        start = time.monotonic()
        task_arn = None
        token = None
        while time.monotonic() - start < timeout:
            if task_arn is None:
                arns = ecs.list_tasks(cluster=cluster, serviceName=service)["taskArns"]
                if arns:
                    task_arn = arns[0]
                    tid = task_arn.split("/")[-1]
                    stream = f"{container}/{container}/{tid}"
                    self.stdout.write(f"  watching log stream {stream}")
            else:
                tid = task_arn.split("/")[-1]
                stream = f"{container}/{container}/{tid}"
                try:
                    kw = {"logGroupName": log_group, "logStreamName": stream, "startFromHead": True}
                    if token:
                        kw["nextToken"] = token
                    r = logs.get_log_events(**kw)
                    for ev in r["events"]:
                        if "Restore complete" in ev["message"] or "WebUI is healthy" in ev["message"]:
                            self.stdout.write(self.style.SUCCESS(f"  > {ev['message']}"))
                        elif "persistent-root" in ev["message"]:
                            self.stdout.write(f"  > {ev['message']}")
                        if "Restore complete" in ev["message"]:
                            return
                    if r["nextForwardToken"] != token:
                        token = r["nextForwardToken"]
                except ClientError:
                    pass
            time.sleep(5)
        raise CommandError("did not see 'Restore complete' in time")

    def _dump_log_stream(self, logs, log_group: str, stream: str):
        try:
            token = None
            while True:
                kw = {"logGroupName": log_group, "logStreamName": stream, "startFromHead": True}
                if token:
                    kw["nextToken"] = token
                r = logs.get_log_events(**kw)
                for ev in r["events"]:
                    self.stdout.write(f"  > {ev['message']}")
                if r["nextForwardToken"] == token:
                    break
                token = r["nextForwardToken"]
        except ClientError as e:
            self.stdout.write(self.style.WARNING(f"  log fetch err: {e}"))

    def _ssm_exec_capture(self, target: ResolvedAwsTarget, cluster: str, task: str,
                          container: str, inner: str, timeout: int) -> str:
        creds = target.session.get_credentials().get_frozen_credentials()
        env = os.environ.copy()
        env["AWS_ACCESS_KEY_ID"] = creds.access_key
        env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
        env["AWS_SESSION_TOKEN"] = creds.token
        env["AWS_DEFAULT_REGION"] = target.aws_region
        cmd = ["aws", "ecs", "execute-command",
            "--cluster", cluster, "--task", task, "--container", container,
            "--interactive", "--command", f"bash -c {shlex.quote(inner)}"]
        feeder = subprocess.Popen(["sleep", str(timeout + 60)], stdout=subprocess.PIPE)
        try:
            proc = subprocess.run(cmd, env=env, stdin=feeder.stdout,
                capture_output=True, text=True, timeout=timeout)
        finally:
            feeder.terminate()
            feeder.wait()
        if proc.returncode != 0:
            raise CommandError(f"ECS exec failed (rc={proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
        return proc.stdout

    def _parse_upload_trailer(self, stdout: str):
        sha = raw = comp = None
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("SHA="):
                sha = line.split("=", 1)[1]
            elif line.startswith("RAW_SIZE="):
                raw = int(line.split("=", 1)[1])
            elif line.startswith("COMP_SIZE="):
                comp = int(line.split("=", 1)[1])
        if not (sha and raw and comp):
            raise CommandError(f"Could not parse upload trailer; got SHA={sha} RAW={raw} COMP={comp}")
        return sha, raw, comp
