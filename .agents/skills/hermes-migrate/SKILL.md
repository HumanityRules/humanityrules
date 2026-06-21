---
name: hermes-migrate
description: Copy a hermes_agent app's persistent-root state from a source DOH environment to a destination DOH environment. Use when the user asks to migrate, clone, copy, or move a hermes app between envs (e.g., default -> sandbox, dev -> prod, or across AWS accounts).
---

# Migrate a Hermes Agent app between environments

The migration is a five-phase pipeline driven by the `doh_hermes_migrate` management command. Your job is to gather inputs, sanity-check them, then drive the command and report results.

## Why this is non-trivial

A hermes app's state lives in two places:

- **EFS checkpoint**: `/hermes-checkpoint/rootfs.tar.zst` — written on SIGTERM, read on boot.
- **Host bind-mount**: `/var/lib/humr/hermes-roots/<app>` on the EC2 container instance — the live persistent root.

`persistent-root-runner.sh` writes a fresh checkpoint on SIGTERM. So if you stage a tar onto the dest EFS while the dest container is running and then stop it, the runner will overwrite your tar. The pipeline avoids that by stopping dest first, then staging via a temporary Fargate task that does **not** run the hermes image.

## Inputs you must gather

Ask the user only if missing — never grill. Reasonable defaults are noted.

1. **App slug** (required) — same on source and dest.
2. **Source env** (required) — name + env slug (e.g., `Humanity Rules Sandbox` + `default`).
3. **Dest env** (required) — name + env slug.
4. **Same AWS account?** — default **yes**. Only ask if the user hasn't said either way and the env names hint otherwise. If yes, accept a single `--account`. If no, you'll pass `--source-account` and `--dest-account` separately, and the command will create the bridge bucket in the source account with a cross-account read policy for the dest account's root.
5. **Local-DB knowledge.** If either env isn't in the local control plane's DB (typical for prod-controlplane-managed envs), use raw mode for that side (`--source-aws-*` / `--dest-aws-*`). Find the four values via `doh_query` against the *other* control plane (or by reading the env's CloudFormation stacks).

## Pre-flight checks (do these silently before running)

- Both source and dest hermes services exist and use the `hermes_agent` template (`grep HUMR_APP_TEMPLATE`-style check, or just check that `/hermes-checkpoint` and `/hermes-persistent-root` mounts exist in the dest task definition).
- Source service has 1 RUNNING task. If it's at desiredCount=0, ask before scaling up — the user may have stopped it deliberately.
- Free disk on dest EC2 instance: `df -h /var/lib/humr` via `doh_node_shell` — restoring needs roughly 2-3x the compressed tar size in free space.
- If the dest persistent root has user data the user might care about, surface its size + last-mtime and confirm before proceeding.

## Driving the command

The default phase chain is `upload,stage,host-clear,finalize,verify`. Run it as one invocation:

```bash
uv run manage.py doh_hermes_migrate \
    --account "<account>" \
    --source-env <src-slug> --dest-env <dst-slug> \
    --app <app-slug> \
    --stop-dest --i-know-this-overwrites-dest
```

Cross-account variant:

```bash
uv run manage.py doh_hermes_migrate \
    --source-account "<src-account>" --source-env <src-slug> \
    --dest-account "<dst-account>" --dest-env <dst-slug> \
    --app <app-slug> \
    --stop-dest --i-know-this-overwrites-dest
```

Raw mode for one side (when local DB doesn't know it):

```bash
... --dest-aws-account-id <id> --dest-aws-external-id <uuid> \
    --dest-aws-region us-east-1 --dest-env-slug <slug> ...
```

State persists across runs in `/tmp/hermes-migrate-<app>.json` — if a phase fails, you can rerun a single phase with `--phases <name>`. The same `--stop-dest` flag is needed when rerunning `stage`.

After verification, ask whether to run `--phases cleanup` — this empties + deletes the bridge bucket and removes the temporary stager IAM role in the dest account. Cleanup is opt-in because keeping the bucket around for a few hours is useful if you discover a problem.

## Cross-account safety

If source and dest are in different AWS accounts:

- The bridge bucket is created in the **source** account with a bucket policy granting `s3:GetObject` + `s3:ListBucket` to `arn:aws:iam::<dest-account>:root`.
- That root grant is scoped to *this bucket only*. The bucket has public-access-block enabled and a 7-day lifecycle.
- If the user wants extra paranoia, suggest pre-creating a bucket with a tighter policy (only the dest stager role's ARN as Principal) and passing `--bridge-bucket`.
- Do **not** make the bucket public. If the user explicitly asks for a public bucket as a "quick" workaround, push back: enabling public access on a bucket holding a full container rootfs (with secrets, tokens, SSH keys) is a leak. Offer the cross-account policy instead — same number of clicks, no leak risk.

## What the command does (so you can answer "why is it stuck")

1. **upload** — ECS-exec into the source hermes container; `tar --zstd ... | aws s3 cp - s3://bucket/rootfs.tar.zst`. Source STS creds are forwarded into the inner shell. Captures sha256 + raw size + compressed size.
2. **stage** — Scales dest service to 0 and waits for tasks to drain. Creates a one-shot Fargate task in the dest env (image `public.ecr.aws/aws-cli/aws-cli`, with `entryPoint=["/bin/sh","-c"]`) that mounts dest EFS root, downloads the object, verifies sha + size, atomic-mvs into `/efs/deployments/<app>/checkpoint/rootfs.tar.zst`.
3. **host-clear** — `rm -rf /var/lib/humr/hermes-roots/<app>` on every ACTIVE container instance in the dest cluster, via `AWS-RunShellScript` SSM document. *Why this is non-optional:* `persistent-root-runner.sh` only restores from the checkpoint when the host bind-mount is empty (`is_empty_dir` branch). If we leave dest's old root in place, the runner takes the `reuse` branch and never reads our staged tar — the migration silently no-ops.
4. **finalize** — Scales dest service back to its previous desiredCount and watches the log stream until it sees `[persistent-root] Restore complete`.
5. **verify** — ECS-exec into the new dest task, `du -sh` on `/hermes-persistent-root/{workspace,home/linuxbrew}`, plus `df -h` and `ls /hermes-checkpoint/`. Compare sizes against what the user expected from source.

## Common failure modes

- **`The Session Manager plugin was not found`**: the operator's machine is missing the SSM plugin. `brew install --cask session-manager-plugin`.
- **upload trailer parse fails / SHA missing**: the source SSM channel was disconnected mid-stream. Rerun `--phases upload`; the bucket is reused via state file.
- **stager `command not found: aws`**: someone changed `STAGER_IMAGE` to a base image that doesn't have awscli — keep `public.ecr.aws/aws-cli/aws-cli`.
- **`sha != expected` in stager logs**: source-side tar was non-deterministic (live writes), or the upload was truncated. Rerun the whole pipeline; SQLite WAL during tar is the most likely source of mismatch — tell the user to quiesce the source if they hit this twice.
- **`Restore complete` never appears**: dest's persistent root wasn't actually empty (host-clear ran on wrong instance, or there are multiple container instances). Rerun `--phases host-clear` and check `--list` from `doh_node_shell`.
- **CrossAccount AccessDenied on s3:GetObject**: the bridge bucket was created before the cross-account flag was set. Delete the bucket (or rerun upload with `--bridge-bucket <new-name>`) and try again.

## What you should *not* do

- Don't try to be clever and stage the tar via the dest hermes container's `/hermes-checkpoint` mount. The runner will overwrite it on shutdown. Always use the temporary Fargate task.
- Don't `rm -rf /var/lib/humr` — only the per-app subdirectory.
- Don't skip `--i-know-this-overwrites-dest`: surface to the user that dest data will be lost and get explicit assent.
- Don't run `cleanup` automatically — keep the bridge bucket for a few hours in case the user spots a problem.
