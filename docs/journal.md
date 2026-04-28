# DevOpsHero Development Journal

## 2026-04-27 22:29 - [DevEx] Move `policy_proxy/` into `template_repos/`

**Conversation:** [2026-04-27-2229-8207d57a.md](conversations/2026-04-27-2229-8207d57a.md)

Repo housekeeping: `policy_proxy/` was a top-level directory at repo root, peer to `devopshero_app/`, `infra_devopshero/`, `lambdas/`. It is now `template_repos/policy_proxy/`, peer to `template_repos/doh_dind/`, `template_repos/hermes_agent/`, `template_repos/openclaw_agent/`. Pure relocation — no behavioral change in build, deploy, or runtime.

**The framing question.** "Should `policy_proxy/` live under `template_repos/`?" turns on what `template_repos/` actually means today. The original intent (per migration `0033_add_app_template.py` and `settings.TEMPLATE_REPOS_DIR`) was "AppTemplate-backed source repos that DOH clones via `file://` at customer-app deploy time and pushes to per-app ECR." `hermes_agent/` and `openclaw_agent/` fit that exactly. But `doh_dind/` already broke the strict reading: it's a DOH-owned utility image, *not* AppTemplate-driven, built out-of-band via `manage.py doh_build_prebuilt_image --source-dir template_repos/doh_dind …` and referenced by tag from a sidecar entry on AppTemplates. Once `doh_dind/` is in `template_repos/`, the directory's *de facto* meaning is "DOH-owned container source trees that ship into customer accounts." `policy_proxy/` is exactly that shape — built per-env, pushed to `doh/{env_slug}/policy-proxy:{POLICY_PROXY_IMAGE_VERSION}`, lives outside the AppTemplate clone path entirely (its presence is signaled by `image_source="policy_proxy"` on a container entry, which `appconfig.py` and `deploy_app.py` resolve directly). So categorically: same bucket as `doh_dind/`, belongs in the same directory.

**Why having it at repo root was actively misleading.** Repo-root peers like `devopshero_app/`, `infra_devopshero/`, `lambdas/` are top-level platform components — the Django app, the control-plane CDK, the Lambda functions. Putting `policy_proxy/` next to those overstates what it is: it's a small FastAPI proxy that ships *into* customer ECS tasks alongside other co-deployed images. Putting it next to `doh_dind/` accurately reflects that role.

**Mechanical changes (small surface area).**

- `git mv policy_proxy template_repos/policy_proxy` — 19 file renames, history preserved. `git mv` also dragged the untracked `.venv/`, `.pytest_cache/`, `.DS_Store` along physically; those got deleted in a follow-up since they're gitignored anyway and reproducible from `uv.lock`.
- `devopshero_app/services/infra_customer/deploy_app.py:38`: `POLICY_PROXY_SOURCE_DIR = Path(__file__).resolve().parents[3] / "policy_proxy"` → `… / "template_repos" / "policy_proxy"`. Stuck with the `parents[3]`-relative pattern instead of swapping in `settings.TEMPLATE_REPOS_DIR / "policy_proxy"` — the existing constants in this file don't import Django settings at module load, and consistency beat symmetry. (Also: `settings.TEMPLATE_REPOS_DIR` is semantically the AppTemplate clone root; `policy_proxy` is not cloned through that path, so the symmetry would be misleading anyway.)
- `template_repos/policy_proxy/README.md`: two `cd policy_proxy` snippets in the local-dev section retargeted to `cd template_repos/policy_proxy`.

**What deliberately stayed unchanged.**

- `Dockerfile` — `COPY policy_proxy ./policy_proxy` is build-context-relative, works at any host path.
- `docs/policy_proxy_design.md` — only path-shaped reference is the runtime URL `/__policy_proxy/healthz`, not a directory; the AppTemplate field name `policy_proxy: true` isn't a path either.
- `lambdas/policy_proxy_auth/README.md` — references its own dir (`cd lambdas/policy_proxy_auth`); the auth Lambda did not move.
- The `pyproject.toml` / `uv.lock` / standalone uv-project shape of `policy_proxy/` — kept self-contained (own FastAPI/uvicorn/httpx deps, separate from the Django root project) because it ships as its own Docker image with hash-pinned `requirements.txt`. Not a candidate for absorption into the root pyproject; that would pollute the control-plane image with FastAPI's transitive surface for no reason.
- Anything in `docs/conversations/*.md` referencing `policy_proxy/` paths — those are immutable session transcripts; treated like git history.

**Verification:**

- 27/27 policy_proxy unit tests pass at the new path (after `rm -rf .venv && uv sync` to refresh the venv — the carried-over venv had stale absolute paths in its activation scripts, which is expected uv behavior on directory rename).
- `POLICY_PROXY_SOURCE_DIR` resolves to a real directory and contains both `Dockerfile` and `policy_proxy/app.py`; verified by importing `deploy_app` under `DJANGO_SETTINGS_MODULE` and printing the constant.
- `git status` shows clean renames + the one path-constant edit; no orphan files.
- Linter clean.

**Naming caveat parked for later.** `template_repos/` is now genuinely a misnomer — the directory holds two non-template entries (`doh_dind/`, `policy_proxy/`) and two AppTemplate-backed ones (`hermes_agent/`, `openclaw_agent/`). A more accurate name would be `bundled_images/` or `vendored_images/` or `shipped_images/`. Not renamed in this session because (a) it's a much bigger surface — `settings.TEMPLATE_REPOS_DIR`, the `source_repo_path` migration help-text, every conversation/journal reference, the deploy-form template — and (b) the misnomer is mostly cosmetic now that the actual semantics ("DOH-owned image source trees") are the same for everything in there. Worth doing in a dedicated session if/when there's appetite.

**Key points:**

- The "template_repos" directory has been quietly broadening its meaning since `doh_dind/` landed; this move makes that drift explicit. Decisions about what belongs there should now use the rule "is this a DOH-owned container source tree that ships into a customer account?", not the original "is this a clone-time AppTemplate repo?". The AppTemplate clone path (`settings.TEMPLATE_REPOS_DIR / source_repo_path`) is one *consumer* of this directory, not its *purpose*.
- Path constants for repo-relative resolution: `Path(__file__).resolve().parents[N]` is the prevailing pattern in `infra_customer/`, even though `settings.TEMPLATE_REPOS_DIR` exists. Don't mix the two in one file just for symmetry — pick whichever is already there. (If we ever add a new infra-side constant pointing into `template_repos/`, that's the moment to consider whether to import settings; for a one-line tail-edit it isn't.)
- `git mv` carries untracked siblings of the moved tracked files along physically (the `.venv` ride-along surprised me; expected `git mv` to touch only tracked entries). Useful in this case because the dev artifacts came too, but the mental model is "git mv is a `mv` plus index update", not "index update only."
- When moving a self-contained uv project, the venv has hardcoded absolute paths in its activation scripts and Python shebangs. `uv sync` after the move is mandatory if you want to run anything in it. Tests confirm in ~2-3 s.
- The naming-debt tradeoff (`template_repos/` is now misnamed but renaming is invasive) is a recurring shape: directory names ossify because their referents move into the test/migration/doc surface area. Worth flagging but not always worth fixing on the same patch.

## 2026-04-27 20:17 - [DevEx] Rename `retry-env-provisioning` → `redeploy-env` and tighten its guard set

**Conversation:** [2026-04-27-2017-2bdda279.md](conversations/2026-04-27-2017-2bdda279.md)

Companion to the `redeploy-app` rename earlier in this session. The naming friction was the same — operators read "retry" and assume failure-recovery, and the journal had already flagged this — but the *behavior* fix is what makes this entry worth writing on its own. The pre-rename `retry-env-provisioning` had a silent footgun the rename forced into the open.

**The latent bug:**

The pre-rename handler only blocked `PENDING` and `PROVISIONING`, then flipped *everything else* to `PENDING`. That includes `TEARDOWN_PENDING`, `TEARING_DOWN`, and `DISCARDED`. Two of those are dangerous, one is meaningless:

- `TEARING_DOWN → PENDING` would put the environment teardown executor and the provisioning executor on the same CFN stack at the same time. Race condition with destructive blast radius.
- `TEARDOWN_PENDING → PENDING` is "user clicked teardown, then ran the CLI" — racing the worker pickup, but in a way that depends on which executor reads the row first. Indeterminate.
- `DISCARDED → PENDING` is asking the worker to provision an env that was deliberately abandoned. No active harm, but not a real operation.

None of these had been reported as an incident, but they were one careless operator-typo away. Worth fixing under cover of the rename instead of in a separate "bug fix" PR that nobody would think to write.

**The new guard set:**

Switched from a deny-list (block PENDING + PROVISIONING) to an explicit allow-list:

- **Allow:** `DRAFT`, `ERROR`, `READY`. The third is the operator's actual common-case need ("re-converge a working env after a CDK template change") and is what the pre-rename command de-facto already supported.
- **Soft no-op:** `PENDING` (already queued — warn and exit 0).
- **Hard-block:** `PROVISIONING`, `TEARDOWN_PENDING`, `TEARING_DOWN`, `DISCARDED`.

Allow-list semantics also mean any future addition to `Environment.Status` lands on a "unexpected status" error rather than silently flipping to PENDING. Defensive against the next status-enum addition.

**Why this is *not* a clean parallel to `redeploy-app`:**

The redeploy-app rename was an architectural change — the CLI was mutating an existing Deployment row in place when it should have been cloning to a new row, and the new code mirrors `app_deployment_redeploy` in `views/apps.py`. There's no analogous "Redeploy" button or view for environments — the agent's `provision_environment` tool is the closest equivalent, and it's *more* restrictive than even the new CLI (it refuses `READY` outright). So `redeploy-env` is intentionally not a strict mirror of any UI flow; it's the SRE escape hatch designed to be looser than chat-driven re-provisioning.

**Open question parked:** the agent tool's refusal of `READY` is now in observable disagreement with the CLI. Two reasonable resolutions:

1. Tighten the CLI to match the agent (drop `READY`). Means losing the most common operator capability — re-converging a working env from the command line — and nobody asked for that. Probably wrong.
2. Loosen the agent to accept `READY` with a confirmation step. Means the chat product needs a "are you sure you want to re-converge a healthy env?" pattern, which it doesn't currently have. Bigger change, separate session.

Left both as-is for now. The CLI is the right venue for "I know what I'm doing, re-run CFN" and the agent is the right venue for guided setup; the divergence reflects that.

**What was *not* changed:**

- **No `--force` flag for the hard-blocks.** If the env is `TEARING_DOWN`, the operator wait-or-fix-the-teardown is correct; offering an override invites people to use it.
- **No status_message audit trail expansion.** Kept the existing `f"Redeploy triggered via doh_control (was: {old_status})"` shape, mirroring how the rest of `doh_control` writes to status_message.
- **No new args.** Old `--slug` + `--aws-account` are still the right primary keys for an environment in the CLI; nothing in the rename touched lookup ergonomics.

**Skill-doc state:**

`prod-manage` SKILL example was the only doc that needed updating (the `manage-commands` SKILL files have been describing this command as "redeploy" for months — they were ahead of the code, same as for `redeploy-app`). First draft included the allowed/blocked status set as an inline shell comment; trimmed back to a one-liner since the comment was redundant with `--help` output and would drift if guards change.

**Verification:** `uv run manage.py doh_control redeploy-env --help` parses cleanly. Parent `doh_control --help` lists `redeploy-env` and `redeploy-app` adjacent, with the old `retry-env-provisioning` name removed entirely. Lints clean. Guard logic is straightforward enough that no integration test was added — the surface is "string compare statuses, save the row", and the existing `_handle_create_env`/`_handle_teardown_env` already exercise the row write.

**Key points:**

- A rename was the cheapest way to ship a behavior fix that was technically pre-existing. The original silent-flip-to-pending of `TEARING_DOWN` was the kind of bug nobody writes a separate ticket for, but it would have eventually caught someone. Combining the rename with the tightening means the "you're using a different command now" prompt naturally surfaces the new guard behavior in operator memory.
- Allow-list + explicit "unexpected status" branch is the right shape for any code that flips lifecycle states. The pre-rename code had deny-list semantics, which fail-open as the status enum grows. We now have *two* commands (`redeploy-env`, `redeploy-app`) using allow-list shape; if a third appears we should make it a helper.
- The CLI vs agent divergence on `READY` is a feature, not a bug — but it's the kind of feature that needs explicit documentation. If we don't write down "the CLI is the SRE escape hatch and is intentionally looser than the agent tool", the next person to look at it will assume it's an oversight and try to "fix" it. Note added to the entry; consider adding it to a future ops doc when one materializes.
- Pattern emerging across the last two sessions: `doh_control` subcommands are settling into a `<verb>-<noun>` shape (`redeploy-app`, `redeploy-env`, `teardown-app`, `teardown-env`, `deploy-app-template`, `create-env`). Worth keeping that consistent — when we add the next subcommand, default to that shape unless there's a reason not to.

## 2026-04-27 19:57 - [DevEx] Rename `retry-app-deployment` → `redeploy-app`, with semantics matching the UI's Redeploy button

**Conversation:** [2026-04-27-2017-2bdda279.md](conversations/2026-04-27-2017-2bdda279.md)

The "rename deferred" follow-up flagged in this morning's [DinD orphan-reap fix entry](#2026-04-27-1631---deployment-persist-hermes-tool-container-state-across-task-restarts-via-dind-side-efs-snapshots): `doh_control retry-app-deployment` had a misleading name *and* mismatched semantics. Renamed to `redeploy-app` and rebuilt the implementation so it matches `app_deployment_redeploy` in `devopshero_app/views/apps.py` exactly. No backward-compat alias.

**Why the old command was wrong (not just badly named):**

The UI's "Redeploy" button creates a new `Deployment` row cloned from a source deployment. The CLI did the opposite — *mutated the existing row's status back to PENDING in place*. Three bad consequences fell out of that single decision:

- **History was destroyed.** The failed/succeeded source row was overwritten, so the deployment list lost a record of what actually happened.
- **`image_tag` was reused.** A fresh `image_tag = f"{slug}-{shortref}-{timestamp}"` is what forces ECR to push a new layer and ECS to roll the service. Mutating in place kept the old tag, which meant ECR cache hits and (depending on `imagePullPolicy` and SHA collisions) sometimes no actual rollover.
- **`SUCCEEDED` was rejected as a redeploy source.** Made sense if you read the name as "retry a *failure*", but in practice the most common reason to redeploy is "the code changed, push it again" — exactly the SUCCEEDED → SUCCEEDED case. This morning's session burned cycles flipping the deployment status to PENDING by hand specifically to bypass this guard.

**Behavior change checklist (CLI ↔ UI parity):**

- Source statuses now allowed: `SUCCEEDED`, `FAILED`, `TORN_DOWN`. Same set as the view.
- Refuses if any deployment for the app is in `IN_PROGRESS_STATUSES` (not just the latest). Mirrors the view's `Deployment.objects.filter(app=app, status__in=IN_PROGRESS_STATUSES).exists()` guard.
- Refuses if `app.status == PENDING_REMOVAL`.
- Creates a new `Deployment` row cloning `blueprint`, `environment`, `subdomain`, `git_ref` (with `app.branch` fallback) — fresh `image_tag` with a current timestamp.
- `--created-by` for audit attribution, reusing the existing `_resolve_created_by` helper from `deploy-app-template` (first admin in the org → any superuser → explicit override). Same convention as the sibling command, since management commands have no `request.user`.

**Source-deployment resolution (the new ergonomics question):**

The UI takes a `deployment_id` from a button click — unambiguous by construction. The CLI doesn't have that luxury. New `_resolve_redeploy_source` helper resolves in this order:

1. `--deployment <uuid>` wins, and is verified to belong to `app`.
2. Else filter by `--env [+ --aws-account]` and pick the latest concluded deployment in that env.
3. Else if the app has been deployed to exactly one environment, use that one.
4. Else error: "App has been deployed to multiple environments (foo, bar); pass --env to pick one".

The single-env fall-through means the most common case (`redeploy-app --app simple-dashboard`) just works without an extra flag, while ambiguous cases fail loudly instead of silently picking. Mirrors the `--aws-account` disambiguation pattern already used by `deploy-app-template`.

**What was *not* changed (deliberate):**

- **`retry-env-provisioning`** has the same naming-vs-behavior friction (refuses to re-run a successful provisioning) but envs and apps are different lifecycle shapes — env provisioning is a CloudFormation stack update and idempotent in CFN, so the "retry" framing is actually defensible. Left alone for now.
- **No backward-compat alias for `retry-app-deployment`.** A hidden alias would invite the agent to keep using the old name and re-learn the wrong mental model. Clean break is cheaper than a graceful deprecation here — only invocations are in agent skill docs (already updated) and journal/conversation logs (historical, fine to leave).
- **No `--git-ref` or `--branch` override.** UI doesn't have one; not a real gap. If we ever want "redeploy at a different ref", that's `deploy-app-template` territory — the UI conflates this with the "Deploy from template" flow rather than the Redeploy button, and we should mirror that.

**Skill-doc state:** the `manage-commands` SKILL files (`.claude/`, `.agents/`, `.codex/`) already described `doh_control` as supporting "redeploy". They were ahead of the code — written aspirationally during a prior session and never reconciled. The `prod-manage` SKILL was the one that needed touching; updated its example block to show `redeploy-app` with three common shapes (default / `--env` / `--deployment`).

**Verification:** `uv run manage.py doh_control redeploy-app --help` parses cleanly. Parent `doh_control --help` no longer lists `retry-app-deployment`; `redeploy-app` shows with the new help text. Lints clean. End-to-end against a live deployment not run — same code paths as `deploy-app-template` once the row is created (which is exercised every UI deploy), so the risk is in argparse + selection logic, both of which are exercised by the parser smoke check.

**Key points:**

- The naming friction was a leading indicator, not the bug. The actual defect was that the CLI's mental model (`retry = mutate to pending`) and the platform's mental model (`redeploy = new row with fresh image_tag`) had drifted. Once the names are aligned, the behavior naturally has to follow — or the next agent reads `redeploy-app` and assumes UI semantics, gets surprised, and we're back to writing one-off snippets.
- "CLI parity with UI X" is becoming a small but recurring pattern (`deploy-app-template` mirrors "Deploy from template", `redeploy-app` mirrors "Redeploy", `teardown-app --remove-app` mirrors "Remove App"). When we keep the helper functions composable (`_resolve_created_by`, `_resolve_environment`, soon `_resolve_redeploy_source`), each new mirrored command costs ~80 lines instead of 200.
- Resolution-priority chains (`--deployment` > `--env` > "single-env fall-through" > "ambiguous error") read as a natural escalation from "I know exactly which one" to "infer it" to "fail loudly". Keeping this pattern consistent across commands means the agent can guess the right invocation without re-reading the docstring every time.
- Journal note for whenever `retry-env-provisioning` gets revisited: the same shape applies — there's almost certainly a `redeploy-env` semantic hiding behind it, and the right rename will become obvious once a session needs it. Not before.

## 2026-04-27 18:51 - [DevEx] Install bash + coreutils on the doh-dind image so doh_app_shell / doh_app_exec work against the DinD sidecar

**Conversation:** [2026-04-27-1851-ce940fcc.md](conversations/2026-04-27-1851-ce940fcc.md)

`doh_app_shell` and `doh_app_exec` couldn't target the `docker-dind` container because the image is Alpine-based (`docker:26.1.0-dind`) and we'd only `apk add`'d `python3 py3-psutil zstd` — there was no `/bin/bash` and no GNU `timeout`. Adding `bash coreutils` to the apk install line fixes both commands; bumped `DOH_DIND_IMAGE_VERSION` 0.2.0 → 0.2.1 and re-pushed.

**Why both commands actually need bash:**

- `doh_app_shell` defaults to `--command /bin/bash` — soft requirement, the user could pass `--command /bin/sh` as a workaround.
- `doh_app_exec` is a hard bash dependency. Its SSM `--command` is `bash -c {wrapper}`, and the wrapper itself does `bash /tmp/doh_exec_script.sh`. Neither layer respects an override flag, so without `/bin/bash` the call dies with `bash: not found` before any user logic runs. There's no escape hatch.

**Why coreutils too:**

The wrapper builds `timeout --preserve-status {N}s {inner}` when `--timeout` is set. `--preserve-status` is GNU-only — busybox's `timeout` doesn't accept it. Without coreutils, any `--timeout N` invocation would fail at the flag-parse stage. Verified live: with coreutils installed, `doh_app_exec --timeout 3` against a 30-second `sleep` returned `exit_code: 143` (SIGTERM) with the post-sleep echo correctly absent. That's `timeout --preserve-status` propagating the killed-process exit code rather than the default 124 — exactly what the wrapper assumes.

**What we deliberately didn't add:**

- **`sudo`.** The wrapper wraps with `sudo -EH -u USER bash -c ...` when `--as USER` is set or the task definition's `user:` field is non-root. Our `_DOCKER_DIND_CONTAINER` in `seed_app_templates.py` doesn't set a `user:`, so it runs as root, `_task_container_user` returns `None`, `run_as` becomes `None`, and the sudo branch is never taken. Operator could only hit it by explicitly passing `--as someuser`, which makes no sense for the DinD sidecar. Skipped to keep the layer small.

**Side effects on existing runtime:** none.

- `apk add bash` doesn't change `/bin/sh` — that stays a busybox symlink to `ash`. Our `entrypoint.sh` (`#!/bin/sh`) and `dockerd-entrypoint.sh` keep running under busybox ash exactly as before.
- `snapshotter.py` is Python; doesn't care.
- Image size delta: bash ~5 MB extracted, coreutils ~5 MB. Noise against the ~470 MB `docker:dind` base.
- Snapshot impact: zero. The snapshotter only `docker export`s `hermes-*` tool containers; the DinD image's own layers are never snapshotted.

**Verification (full E2E):**

Built and pushed `0.2.1` to Humanity Rules Sandbox / default env ECR via `doh_build_prebuilt_image`, re-seeded templates, deployed a fresh `hermes-vmendi01` from `hermes-personal` into Humanity Rules / default. Then against the live `docker-dind` container:

- `bash --version` → `GNU bash, version 5.2.21(1)-release (aarch64-alpine-linux-musl)` at `/bin/bash`.
- `which timeout` → `/usr/bin/timeout` (the GNU one from coreutils, not busybox's).
- `doh_app_exec` returned `exit_code: 0` end-to-end — meaning the SSM-side `bash -c {wrapper}` and the inner `bash /tmp/doh_exec_script.sh` both resolved.
- `doh_app_exec --timeout 3` against a 30-second sleep returned `exit_code: 143` after ~3 s.

**Key points:**

- `doh_app_exec`'s wrapper is bash-only at two nested layers (the SSM `--command` and the inner script invocation), neither overridable by flags. Any container we want to script against must have `/bin/bash`. Worth keeping in mind for any future Alpine-derived images we add to a task.
- `--preserve-status` is the load-bearing GNU-ism in the wrapper. Busybox `timeout` would silently accept `-s SIG -k KILLDELAY DURATION CMD` but reject `--preserve-status` outright. If we ever want this command to work on a busybox-only image (security-minimal containers, distroless, etc.), the cleaner fix is to portable-ize the wrapper rather than ship coreutils everywhere — note this for whenever it becomes relevant.
- The DinD container is a slightly weird target for `doh_app_exec` philosophically (its job is dockerd + snapshotter PID 1, not arbitrary script hosting), but in practice this is exactly the access we want when triaging snapshot/restore issues — `docker images`, `ls /var/lib/doh-dind/persistence`, etc. Worth the 10 MB.
- Version bump conflict noted: I'd planned 0.1.1 → 0.1.2, but a parallel commit (`b7a6a6d`, "Persist tool-container state via docker commit; snapshot only periodically") had landed 0.2.0 in the meantime. Bumped to 0.2.1 to keep history monotonic. Standard hazard when image versions live in source-controlled constants and multiple branches ship image-touching changes.

## 2026-04-27 16:31 - [Deployment] Persist Hermes tool-container state across task restarts via DinD-side EFS snapshots

**Conversation:** [2026-04-27-1632-a1dd698f.md](conversations/2026-04-27-1632-a1dd698f.md)

Hermes's agent flagged that pip/apt/npm installs it runs inside the tool container don't persist between tool invocations. Root cause: `container_persistent: false` in `config.yaml.template` plus upstream's per-turn cleanup (`run_agent.py:_cleanup_task_resources`) and 300-second idle reaper (`terminal_tool.py` `TERMINAL_LIFETIME_SECONDS`) tear the tool container down almost immediately. Only `/workspace` survived because it was an EFS bind mount — everything in the container's writable layer was ephemeral by design.

The session walked through several architectures before landing on the chosen one, and the reasoning behind *rejecting* the alternatives is worth capturing because the "obvious" paths have real failure modes that bite after you commit to them.

**Rejected approaches and why:**

- **Just flip `container_persistent: true` + bump `TERMINAL_LIFETIME_SECONDS`.** Only persists across the lifetime of a single Hermes process. ECS task restart (deploy, scale-in, instance drain) wipes DinD's per-task ephemeral storage and everything in it. Fine for turn-to-turn, not for real durability.
- **ECS-managed per-task EBS.** Killed after fetching the AWS docs directly (the [ECS EBS volumes page](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ebs-volumes.html)): "You can attach at most one Amazon EBS volume to each Amazon ECS task, and it must be a **new volume**. You can't attach an existing Amazon EBS volume to a task." Every task replacement gets a fresh blank volume. Useless for cross-restart state.
- **Host-EBS on the ECS-on-EC2 ASG** (which we already run for Hermes, because DinD needs `privileged: true` which Fargate doesn't allow — `deploy_app.py:679-680`). Would give truly continuous state for the lifetime of the EC2 instance, but instance replacement still wipes it. Recovery would require snapshot-on-terminate lifecycle hooks + restore-on-launch userdata — real engineering. Single-AZ, and loses EFS's multi-AZ durability story.
- **Putting `/var/lib/docker` directly on EFS.** Doesn't work at all. Overlay2 (Docker's default storage driver) requires xattrs and overlayfs semantics NFS doesn't provide — you get `EINVAL` at mount time. The daemon's BoltDB metadata stores (`buildkit/`, `containerd/`, `network/files/local-kv.db`) explicitly warn against NFS backing. The `vfs` driver sidesteps overlayfs but still has the BoltDB problem, plus it breaks CoW (a 1 GB image becomes 5 GB if it has 5 layers).
- **Teaching Hermes to "adopt" an existing container on startup.** Would be the ideal for Hermes-only restart, but requires a patch to upstream's `_DockerEnvironment` in `tools/environments/docker.py`. User was (rightly) wary of forking container lifecycle logic.

**Chosen architecture: snapshot-as-image on EFS.**

A new `template_repos/doh_dind/` image layered on `docker:26.1.0-dind` runs a Python snapshotter alongside dockerd:

- **Snapshot format**: `docker export <id> | zstd`. Flat single-layer tarball — no image metadata, no overlay layer count concerns. The restore side re-applies load-bearing ENVs (`PATH`, `LANG`, `POETRY_HOME`) via `docker import --change` because `docker export` drops image config. I verified against `nikolaik/python-nodejs:python3.11-nodejs20` that nothing else in `.Config.Env` is dynamically load-bearing (`GPG_KEY`/`PYTHON_VERSION`/`PYTHON_SHA256` are build-time only).
- **Three concurrent triggers, one `do_snapshot()`**:
  1. `docker events --filter event=die` — container stopping.
  2. `docker events --filter event=start` — orphan reap (snapshot + `docker rm -f` older `hermes-*` siblings of the newly started container).
  3. 15-minute periodic timer, guarded by `docker diff` fingerprint so idle containers are effectively free.
  4. SIGTERM handler that force-snapshots all live tool containers before stopping dockerd.
- **EFS layout** (new third access point under `_HERMES_EFS_CONFIG`):
  ```
  efs/deployments/<app>/docker-persistence/
    latest.tar.zst        # most recent good snapshot
    latest.meta.json      # base_image_ref, fingerprint, ts, container_id
    snapshots/<ts>.tar.zst  # rotated prior snapshots (retention=3)
    incoming.tar.zst      # in-flight write, atomic-renamed to latest
  ```
- **Restore on DinD boot**: if `latest.tar.zst` exists → `zstd -d | docker import - doh-toolbox:latest`. Otherwise pull `$TOOL_IMAGE_BASE` and tag it. Either way, `doh-toolbox:latest` exists locally before the healthcheck marker (`/var/run/doh-restore-ready`) is touched — so Hermes's `depends_on: {docker-dind, HEALTHY}` gate blocks it from doing `docker run` until the image is ready.
- **`DOH_SNAPSHOT_RESTORE=auto|skip|force_rebuild` env** on DinD — the operator escape hatch. `force_rebuild` also deletes `latest.tar.zst`. We picked this because we're deliberately deferring the "base-image rebase workflow" (what happens when `TOOL_IMAGE_BASE` gets bumped, security patches, etc.) until it becomes a real problem; this knob at least lets ops nuke a user's state without hand-editing EFS.

**Why snapshot-as-image (not adopt-existing-container):** Hermes keeps doing `docker run doh-toolbox:latest` with no knowledge that anything changed. Every upstream bump stays a trivial version bump. The cost: Hermes-only restart loses up to 15 min of rootfs churn (the new Hermes creates a fresh container from the last snapshot). Assumed rare enough to accept — if data contradicts that, we add adoption later.

**Why all logic in DinD (no Hermes-side trap):** DinD owns the lifecycle of the tool container — it sees every `start` and `die` regardless of which sibling caused it. Putting the snapshotter trap in Hermes was explored (and accepted after a user push) but rejected once we realized it needed a DinD-side boot-reconciliation backstop anyway for SIGKILL-class failures — at which point owning it twice is worse than once. `essential: true` on the Hermes container means task-level SIGTERM reaches DinD in parallel anyway.

**Secondary plumbing changes:**

- **New `stop_timeout: int | None` on `ContainerConfig`** (`appconfig.py`), threaded through `app_config_builder.py` and into `ecs.ContainerDefinition`'s `stop_timeout` in `deploy_app.py`. Both `hermes` and `docker-dind` set it to 120 so the snapshotter has time to flush a multi-GB `docker export` to EFS before ECS SIGKILLs. ECS defaults to 30s, which isn't enough.
- **`TERMINAL_LIFETIME_SECONDS=86400`** on the Hermes container env. Upstream's idle reaper would otherwise recycle the tool container after 5 min of silence, forcing a fresh `docker run` (and fresh writable layer) on the next call. With 24h, one Hermes process reuses one container all day, letting the snapshotter capture a real evolving writable layer.
- **`TOOL_IMAGE` changed from the dockerhub ref to `doh-toolbox:latest`**. The ghcr/dockerhub base ref moved to `TOOL_IMAGE_BASE` on the DinD container, only consulted as a fallback when no snapshot exists. This separation is small but important — the base ref and the "what Hermes runs" ref are genuinely different concepts now.
- **Prewarm block deleted from `hermes_agent/entrypoint.sh`.** DinD's healthcheck-gated restore makes the Hermes-side `docker pull` redundant.
- **`container_persistent: true` in `config.yaml.template`.** Not strictly required for snapshot-as-image, but it means upstream's per-turn `cleanup_vm()` is skipped, so the container lives longer within a Hermes lifetime → snapshots capture more realistic state.

**Scope parked (deliberately):**

- **Multi-user Slack bots with `group_sessions_per_user: true`** (upstream default, kept). Each user gets their own `task_id` → their own `hermes-*` container → potentially N containers. Three shapes exist (shared snapshot, per-user snapshot via Hermes patch, shared-on-disk via SOUL.md nudges). User wanted to *observe first* before choosing. Design was kept single-container; the "which containers to snapshot" selector is isolated as a 5-line function so we can change it without touching `do_snapshot()` itself.
- **Base-image rebase workflow.** Deferred. `base_image_ref` is stamped into `latest.meta.json` so when the day comes, we can tell which users are on which base and plan migration waves without reverse-engineering tarballs.
- **Bypassing DinD entirely** (host-docker.sock mount). Raised by user, parked for later.
- **Cross-region snapshot replication.** EFS's default multi-AZ durability is accepted.

**Bug caught in dev rollout:** On first deploy, the agent's second `echo` call returned `No such container: <full-id>`. Root cause was a short-id vs full-id mismatch:

- `docker events` emits full 64-char container ids.
- `docker ps --format {{.ID}}` truncates to 12 chars by default.
- `_reap_older_siblings` compared event-id (full) to ps-id (short) with `!=`, which was always true.
- Net: every just-started tool container was immediately snapshotted-and-reaped as its own orphan, and Hermes's next `docker exec` against the full id failed.

Fix: `docker ps --no-trunc` so both sides use full ids. Bumped `DOH_DIND_IMAGE_VERSION` to `0.1.1`, rebuilt and pushed via `doh_build_prebuilt_image`, flipped the Deployment status to PENDING manually (since `doh_control retry-app-deployment` refuses when the deployment is `SUCCEEDED`), redeployed, verified fix live. User noted the command naming is confusing ("retry" implies failure recovery, but the code path is a plain redeploy) — renaming deferred to a follow-up session.

**Remaining known issues (not yet fixed):**

- **13-byte tarball after orphan reap.** `docker rm -f` of a reaped container fires a `die` event → snapshotter tries to `docker export` the already-gone container → the shell pipeline swallows the export failure (zstd exits 0 on empty stream) → 13-byte "snapshot" rotates the real one into `snapshots/` and promotes garbage to `latest.tar.zst`. Two independent mistakes: (a) `shell=True` without `pipefail`, (b) no "recently reaped" set to suppress the redundant die-event snapshot. Both fixes are small. Parked for the next session.
- **Upstream's `--storage-opt is supported only for overlay over xfs with 'pquota'` error** surfaces in logs from Hermes's container-create call. Pre-existing (not introduced by our changes); worth separate investigation if disk limits matter.

**Key points:**

- Snapshot on EFS is the right durability tier for reconstructible state (packages); `/workspace` and `/home/hermeswebui/.hermes` stay continuous-on-EFS because they hold irreplaceable state (user code, agent memory).
- Docker `export`/`import` captures the container's *entire filesystem view* (base image bytes included), not just the writable-layer diff — so snapshot size has a floor ~= base image size regardless of agent activity. The `docker diff` fingerprint guard only skips *identical* repeated snapshots; it doesn't reduce per-snapshot size.
- The `--no-trunc` bug is the kind of thing that's obvious once seen, and near-invisible in code review because the `!=` looks plausible both ways. Prefer structured equality helpers over raw id-string comparison in future.
- `snapshots/` retention exists as crash protection for the write itself (torn tarball during step 1 of `export | zstd | rename`), not as a user-facing time-travel feature. Three copies is enough belt-and-suspenders; restore path doesn't automatically fall back to them (would be a 5-line addition).
- Management command naming surfaced as a real friction point: `retry-app-deployment` / `retry-env-provisioning` both refuse to re-run against successful/ready targets. The commands' actual job is "reset status to pending so the worker picks it up" — they're redeploy commands with a guard in the wrong place. Rename deferred but noted.

## 2026-04-27 11:37 - [DevEx] `doh_app_exec`: non-interactive probe-in-container companion to `doh_app_shell`

**Conversation:** [2026-04-27-1145-9ee7793c.md](conversations/2026-04-27-1145-9ee7793c.md)

`doh_app_shell --command` was built for humans opening a live session, and an agent using it for ad-hoc probes ran into three problems over and over in the preceding hermes-slack debugging work: (1) SSM session banner lines ("Starting session with SessionId...", "Exiting session...", "Cannot perform start session: EOF") interleave with program stdout, and short probes frequently had the interesting line scroll off or never arrive because the session closed too fast; (2) the exec drops into the container as `root`, so `~` resolves to `/root` instead of the service user's home — `load_config()` in Hermes silently reads the wrong config file and returns misleading results, which burned ~20 minutes chasing a phantom "config loader strips keys" bug; (3) you pay three layers of shell quoting (host bash → SSM command → inner bash), making multi-line Python probes painful to write and easy to break.

`doh_app_exec` is a sibling management command designed for scripted/agent use. Core shift: stop modeling this as "open a shell" and model it as an RPC — send a bash script, get back `(exit_code, stdout, stderr)` with no terminal noise mixed in.

**Design choices that matter:**

- **Input on stdin (heredoc) or `--script-file`.** No inline `--command "..."` flag at all. The agent writes the script in a `<<'EOF'` heredoc and it reaches the container verbatim.
- **Sentinels fence the real output.** Server-side wrapper emits `___DOH_EXEC_BEGIN___` / `___DOH_EXEC_END___ RC=<N>` sentinels, and tags each line with `O:` or `E:` (stdout vs stderr) via FIFOs and backgrounded `sed` processes. Client reads the raw SSM output, finds the sentinels, separates streams cleanly. SSM banner lines fall outside the fence and are ignored.
- **Two specific bash traps I hit:**
  - Process substitution (`> >(sed 's/^/O:/')`) is async and `wait` does not join it, so a naïve setup lets the `RC=` line print before trailing `O:` lines. Fix: use named FIFOs with explicit `$!` PIDs and `wait "$PID_O" "$PID_E"` before echoing RC.
  - Closing the subprocess's stdin (`stdin=DEVNULL` or `input=""` in `subprocess.run`) causes SSM to tear down the session with `Cannot perform start session: EOF` **before** the remote script finishes. Fix: pipe a long-running `sleep 3600` into stdin so SSM sees a live input side, and let the session end naturally when the remote wrapper exits.
- **`--as USER` with sensible auto-default.** Defaults to the task definition's `containerDefinitions[].user`. Pass `--as hermeswebui` to probe as the service user so `load_config()`, `~`, env-file loading, etc. behave like the real process. `--as root` (or the default when no user is set in the task def) skips sudo entirely since ECS Exec already enters as root.
- **`--format json` vs `text`.** JSON emits `{"exit_code": N, "stdout": "...", "stderr": "...", "stdout_truncated": bool, "stderr_truncated": bool}` — that's the default shape an agent wants to parse. Text mode prints stdout to stdout, a `--- stderr ---` block to stderr, and `--- exit_code: N` to stderr.
- **`--timeout` uses `timeout --preserve-status`** inside the wrapper, so a runaway script is killed server-side rather than leaving a zombie session.
- **Other ergonomics:** `--cwd`, repeatable `--set KEY=VALUE`, 1 MiB output cap with `stdout_truncated`/`stderr_truncated` flags, `--ignore-exit` to override the default "propagate inner exit code."
- **`DOH_APP_EXEC_DEBUG=1`** prints the raw SSM output before parsing — escape hatch when a probe looks wrong and you want to see what the wire actually delivered.

**What I did NOT add (and why):**

- Persistent-shell mode (keep state between calls). Complicates the RPC model; agents can chain calls with `&&`. Also fights ECS task replacement.
- Structured (JSON-input) script spec. The shell is already the API; more abstraction = more surprise.
- Colored output. Scrapers hate it; humans have `doh_app_shell`.

**Gotchas while writing it:**

- Argparse collision: `--env` is already claimed by `_aws_account_resolver.add_aws_target_args()` for the environment slug. Renamed mine to `--set`.
- Two Python module layouts coexist in the container (`/app` site-packages for the webui, EFS-mounted `/home/hermeswebui/.hermes/hermes-agent` for the agent); the `--as` user matters because `sys.path` and `HOME` both differ.
- Heredoc examples in the docstring must use `<<'EOF'` (single-quoted) so shell variables inside the agent script aren't expanded by the outer shell reading `doh_app_exec`'s example.

**Doc trimming.** First draft of the skill entries defended the design ("stdout is intermixed with SSM banner lines, frequently truncates short probes, and enters the container as root — so `~` resolves to `/root`..."); the user pushed back that it was too long. The revised entries are two lines each: what it is, and which flags it supports. The docstring at the top of the file got the same treatment — one usage example, a one-line flag list, and the "requires" note; no "key differences from doh_app_shell" section.

**Verified against the live hermes-slack deployment:** a multi-statement heredoc running as `hermeswebui` returned clean JSON with all 10 `mcp_sidecar_*` tool names, `exit_code: 0`, no stderr contamination. `--timeout 3` + `sleep 20` returned `exit_code: 143` with only the pre-sleep output captured. `--cwd /tmp` + `--set FOO=bar` behaved as expected.

**Key points:**
- `doh_app_shell --command` was designed for human interactive use; using it for scripted probes is painful because SSM banners, root-by-default exec, and three-layer shell quoting all fight the caller. `doh_app_exec` replaces that use case without changing `doh_app_shell`.
- Sentinel-fenced output separation is the right pattern for any RPC-over-interactive-channel situation: fence the payload, tag each line's stream, the banner lines become discardable noise.
- Bash process substitution is not joinable with `wait`; use named FIFOs with explicit PIDs when you need deterministic ordering between the stream consumers and a final "done" marker.
- SSM sessions care whether the client's stdin is open, not whether bytes are flowing. Give it a live but empty stdin source (e.g. `sleep 3600 | ...`), not `DEVNULL`.
- Default `--as` to the task-def container user (when set). Root-by-default for exec is a trap for any process whose behavior depends on `$HOME` or `$USER`.
- Skill-entry prose should be prescriptive and short: what it is, which flags exist, one example. The "why we made it" belongs in the journal, not in the skill or the command's docstring.

## 2026-04-27 09:09 - [Bugfix] hermes-slack: MCP tools (and slack-bolt) never registered — install timing race in webui bootstrap

**Conversation:** [2026-04-27-0910-9ee7793c.md](conversations/2026-04-27-0910-9ee7793c.md)

Deployed `hermes-slack`, the agent said "I don't have any tools that start with `mcp_`" — no `mcp_sidecar_*` tools offered to the LLM. `config.yaml` had `mcp_servers.sidecar`, the `sidecar-mcp` sibling container answered MCP `initialize` fine, the hermes toolset aliases correctly resolved `sidecar` → `mcp-sidecar` (10 tools). End-to-end wiring was correct *in a fresh Python process*. Only the long-lived WebUI process didn't see the tools.

**Root cause — why "too late" meant forever.** `tools/mcp_tool.py` in hermes-agent has this at module scope:

```python
_MCP_AVAILABLE = False
try:
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    ...
```

Module-level code runs exactly once per Python process, on first import. `_MCP_AVAILABLE` is captured at that moment and cached for the life of the process. Later in `discover_mcp_tools()`:

```python
if not _MCP_AVAILABLE:
    logger.debug("MCP SDK not available -- skipping MCP tool discovery")
    return []
```

So if `mcp` isn't importable at the moment `tools.mcp_tool` first loads, every subsequent `discover_mcp_tools()` call in that process is a no-op — **even after `pip install mcp` completes**, because Python won't re-evaluate the module's top-level `try/except`. The only way to recover is to restart the process.

Now overlay the webui startup sequence:

1. `start.sh` launches `hermeswebui_init.bash` in the background (it creates `/app/venv`, pip-installs base deps, touches `.deps_installed`, and **immediately execs `python server.py` with no join point**).
2. `start.sh` polls for `.deps_installed`, then runs a second `uv pip install` for `hermes-agent[bedrock,mcp]` and `slack-bolt/slack-sdk`.

The second install *always* races server.py's startup. If any import chain in the webui process reaches `tools.mcp_tool` before our extras land — or, equivalently, if `mcp` isn't in site-packages the first time `tools.mcp_tool` loads later — `_MCP_AVAILABLE=False` sticks for the life of the process. Same shape for `slack-bolt`: gateway.run imports it at startup, and a late pip-install never un-breaks the import that already failed.

**Proving the race, not just hypothesizing.** Process start timestamps vs. package install timestamps in the running container:
- `/proc/239` (python server) birth time: `03:34:52`
- `/app/venv/lib/python3.12/site-packages/mcp/__init__.py` mtime: `03:35:11` (19 seconds later)

So the webui process was alive 19 seconds before `mcp` was importable. Whether or not `tools.mcp_tool` was *transitively* imported during that window, the safer claim is: the install was not guaranteed to land before any module import, and the symptom matched the cached-False failure mode exactly.

**Dead ends that felt like the answer but weren't.**
- *"`load_config()` strips unknown keys, so `mcp_servers` gets dropped."* Wrong — `_deep_merge` upstream is permissive (verified against `NousResearch/hermes-agent@v2026.4.16`). My ECS Exec shell saw `mcp_servers: None` only because the shell runs as root (`HOME=/root`), so `get_config_path()` resolved to `/root/.hermes/config.yaml` which didn't exist — a red herring caused by the exec environment, not the webui process's.
- *"MCP toolset name mismatch."* Registry stores tools under canonical `mcp-sidecar` but we pass `sidecar` in `enabled_toolsets`. Resolver *does* follow alias chain (`validate_toolset` returns True for alias names; `get_toolset` calls `get_toolset_alias_target`). Verified via direct probe: `get_tool_definitions(enabled_toolsets=["sidecar"])` returns 10 `mcp_sidecar_*` schemas in a fresh process. Wiring is fine.
- *"The WebUI is caching a session from a pre-fix container."* Session is persistent on EFS, but tool discovery runs per stream. Irrelevant.

**Fix.** Stop trying to install after `hermeswebui_init.bash` has started the server. Instead, `sed`-patch the init script's own `uv pip install` line to include our extras, *before* it runs:

```bash
WEBUI_INIT_PATCHED=/tmp/hermeswebui_init.patched.bash
if ! grep -q 'hermes-agent\[honcho,bedrock,mcp\]' "$WEBUI_INIT_PATCHED" 2>/dev/null; then
    sed 's|"/home/hermeswebui/.hermes/hermes-agent\[honcho\]"|"/home/hermeswebui/.hermes/hermes-agent[honcho,bedrock,mcp]" "slack-bolt>=1.18.0,<2" "slack-sdk>=3.27.0,<4"|' /hermeswebui_init.bash > "$WEBUI_INIT_PATCHED"
    chmod +x "$WEBUI_INIT_PATCHED"
fi
...
"$WEBUI_INIT_PATCHED" > >(grep --line-buffered -v '"path": "/health", "status": 200') 2>&1 &
```

Then the post-launch install blocks in `start.sh` (both the `[bedrock]` one and the slack one) and their sentinel-file bookkeeping were deleted — the extras ride in on the same synchronous chain that feeds `.deps_installed`, so `python server.py` starts up with everything in its venv on the first import.

**Non-obvious gotchas, in the order they bit us.**
- Pattern mismatch: upstream's actual line is `uv pip install "/home/hermeswebui/.hermes/hermes-agent[honcho]"` (quoted, with `[honcho]`). My first sed anchored on `uv pip install /home/hermeswebui/.hermes/hermes-agent ` (unquoted, no extra) — a pattern that never matched. Silent failure — sed returns 0 when nothing matches. Had to pull the real `hermes-webui:0.50.126` image locally (`docker run --rm --entrypoint bash ...:0.50.126 -c "cat /hermeswebui_init.bash"`) to see what was actually there. Lesson: never write a patch-sed against imagined upstream text; always inspect the real file.
- Permission: the container drops to `USER hermeswebui` before start.sh runs. `sed -i` on `/hermeswebui_init.bash` fails silently with `sed: couldn't open temporary file /sedXXXXXX: Permission denied` — the non-root user can't create a temp file in `/`. Switched to non-in-place `sed` writing to `/tmp/hermeswebui_init.patched.bash`, and invoke that copy instead.
- Slack deps in the same install: the gateway imports `slack-bolt` at startup and crashes the container when it's missing (not just Slack — the whole container exits because `start.sh`'s `wait -n` treats any child death as terminal, triggering ECS to circuit-break the deployment after repeated failures). Installing Slack unconditionally (tiny deps, always safe) is simpler than a second sed pattern and removes a second race.
- `[honcho,bedrock,mcp]` — adding `honcho` is deliberate: the upstream install was `[honcho]`, and we want to preserve that extra, not replace it. The original inline `sed` I wrote replaced the whole `[honcho]` bracket with `[bedrock,mcp]`, dropping honcho and breaking the webui. Compose the new extras as a superset: `[honcho,bedrock,mcp]`.

**Key points:**
- Python's module-level `try/except ImportError` caches its result at first import and never re-evaluates it. Any "install dep, then import it later" plumbing that races a long-lived process is a latent bug when the sequence slips.
- If a dep install races a process start, fix it by folding the install into the process's own startup, not by sprinkling more installs after the fact.
- Patch-sed against an upstream file requires inspecting the actual upstream file (pull the image), not guessing. Silent failure is the default; `grep -q` the marker after the sed to turn silent failure into noisy failure if needed.
- Running as non-root in a container means `sed -i` can only patch files whose **parent directory** is writable — `-i` needs to create a sibling temp file. Use redirected sed + `chmod +x` to a writable location (`/tmp/`) and invoke the copy.
- Docker HEALTHCHECK passing + ALB targets unhealthy + ECS task replacement loop (from the earlier hermes-slack bug this session) and agent-reports-no-mcp-tools despite correct config.yaml (this bug) are both "look like something else until you check the timing" classes of failure.

**Working deployment:** https://hermes-slack-mcp4.chsandbox.com

## 2026-04-26 20:51 - [Deployment] Hermes config.yaml is now DOH-owned and regenerated every boot

**Conversation:** [2026-04-26-2052-5cb55dff.md](conversations/2026-04-26-2052-5cb55dff.md)

Flipped `template_repos/hermes_agent/entrypoint.sh` so `~/.hermes/config.yaml` is rewritten from `config.yaml.template` on every container start, instead of the previous "first boot only, user edits preserved" behavior.

**Why this changed.** The trigger was wanting a simple way to temporarily disconnect the hermes terminal from its docker sandbox for a test. `terminal.backend: docker` is sed'd into config.yaml from `TERMINAL_BACKEND="docker"` at `entrypoint.sh:67`, but since config.yaml was written once and then preserved, flipping the var on an existing workspace did nothing — the EFS-backed file kept the old value. Considered adding an env-var escape hatch that rewrites just the `terminal.*` section, but the deeper point is that `terminal.backend` is load-bearing for the sandbox: if user edits to it were respected as "preference," setting `backend: local` inside the sandboxed terminal would be a sandbox escape via config file. So "config.yaml is user-owned" was the wrong invariant to begin with.

**What the swap required checking.** Verified hermes itself never writes config.yaml at runtime: README declares it read-only, entrypoint is the only writer, WebUI settings go to a separate `~/.hermes/webui-mvp/settings.json`, and the journal entry from 2026-04-25 confirms a prior surgical-rewrite approach was already deleted. With no runtime writes from hermes, always-regenerate has no data-loss surface.

**Discussed but not built: a merge system.** Briefly weighed a two-file layering (template-rendered `config.yaml` + user overlay) as the "clean" path. Killed it because there's no concrete user-editable surface today — LLM provider/model/base_url are all DOH-owned env, SOUL.md and WebUI prefs already live in separate files, and MCP servers/tool lists are template-only. Building a merge system for a hypothetical user-config surface is premature; revisit only if one actually appears.

**Change.** Dropped the `[ ! -f "$HERMES_DIR/config.yaml" ]` guard at `entrypoint.sh:91`. The sed block and the Bedrock region append (for `DOH_LLM_PROVIDER=bedrock`) now run unconditionally on every boot. `SOUL.md` and `hermes-agent/` keep their first-boot-only copies — those are real user state (persona edits, user-run `hermes update`). Updated README at both places that claimed config.yaml was preserved across boots.

**Practical consequence.** The `TERMINAL_BACKEND` var at `entrypoint.sh:67` is now a real toggle: change the string, redeploy, and the next task boot picks it up. Left `TERMINAL_BACKEND="local"` in the entrypoint during this session as a deliberate test-state change (see the system-reminder diff in the conversation). DinD sidecar stays up either way, so the `DOCKER_HOST` / `docker info` hard-fail block at lines 51-58 keeps passing and doesn't need to be conditionalized.

**Unresolved.** Did not confirm against upstream hermes source whether `backend: local` is the accepted string for "no sandbox" vs `none` or omitting the key entirely — the existing entrypoint comment ("backend=docker is the only supported mode") is a DOH statement, not a hermes one. Verify before flipping in prod.

**Also this session:** removed `hermes-slack01` with full cleanup (`teardown-app --remove-app --delete-secrets --delete-efs-data --delete-policies`). Removal job `019dcd08-ae59-75f3-87a2-2252179815e5` queued successfully; worker handles the tear-down + cleanup + cascade inline.

**Key points:**
- `config.yaml` is DOH-owned and regenerated on every boot. Security-load-bearing settings (sandbox backend, docker volumes) must not be preserve-user-edits.
- Two-file merge designs are tempting but solve a hypothetical until there's a real user-editable field. Don't build one on spec.
- When a file on EFS is treated as "user state" but actually controls security posture, audit the writer logic — preserving edits is the wrong default.
- `SOUL.md` and `hermes-agent/` remain first-boot-only copies; they're genuine user state and the change is scoped narrowly to config.yaml.

## 2026-04-26 14:44 - [UI] Template deploy form: preselect single-option dropdowns, trim Configuration Summary

**Conversation:** [2026-04-26-1445-4c25d570.md](conversations/2026-04-26-1445-4c25d570.md)

Small UX pass on `/deploy/from-template/<slug>/`. Two unrelated polish items:

**Preselect single-option workspace/environment.** On GET, the form was always initializing `selected_workspace_id=""` / `selected_environment_id=""` with placeholder labels, even when ABAC filtering had narrowed the options down to a single choice. Users with exactly one permitted workspace (or one ready environment) still had to click into the dropdown to pick it. Fix: in `template_deploy.template_deploy_form`, compute `default_workspace_id = workspace_options[0]["id"] if len(workspace_options) == 1 else ""` (same for environment) and feed that through `_selected_label(...)`. Mirrors the existing pattern at the owner field where non-admins are locked to themselves and the single option is pre-selected. POST-error redisplay was left alone — it correctly echoes whatever the user submitted.

**Trimmed Configuration Summary.** Removed the "EFS Mount" row and the "Compute" row from the summary block in `template_deploy_form.html`. EFS mount path is an internal detail nobody reading the summary acts on; compute mode has its own labeled dropdown above the summary, so repeating the selected label below was redundant. CPU / Memory / Containers remain.

**Aside — misread the user once.** First read of "compute EC2 capacity is not necessary" had me delete the EC2 option from `_compute_mode_options()`. User clarified they meant the Configuration Summary row, not the form field. Reverted the options change; the EC2 choice is still offered as a compute mode.

**Key points:**
- Auto-preselecting single-option dropdowns is a small but high-value UX win for deploy forms where ABAC often leaves exactly one permitted target. Pattern already existed for `owner_id`; extended to `workspace_id` / `environment_id` with the same shape.
- The GET path and the POST-error redisplay path in `_handle_deploy` have different contracts: GET should help the user skip obvious clicks; the error path must echo what was submitted verbatim. Keep the preselect logic on GET only.
- When a summary re-displays a form field that's already visible and labeled above it, the summary row is noise. Axe it.

## 2026-04-26 14:10 - [Bugfix] hermes-slack: unpin WebUI from loopback so the ALB can health-check it

**Conversation:** [2026-04-26-1411-9ee7793c.md](conversations/2026-04-26-1411-9ee7793c.md)

Deployed `hermes-slack` to Humanity Rules Sandbox and the deployment hung at `CREATE_IN_PROGRESS` on the ECS service for minutes. Container logs looked perfect (`Hermes Web UI listening on http://127.0.0.1:8787`, internal `GET /health → 200` every 30s), yet the ALB target group was `unhealthy` with `Target.FailedHealthChecks`, and ECS kept replacing tasks in a loop (`"Amazon ECS replaced 1 tasks due to an unhealthy status"`). That combination — container healthy on loopback, ALB target unhealthy on the ENI IP — was the tell.

**Root cause.** `_HERMES_CONTAINER_BASE` in `seed_app_templates.py` was pinning `HERMES_WEBUI_HOST=127.0.0.1` as a "platform constant." That value is only correct for templates where a policy proxy fronts the task (sharing the task network namespace) — the proxy can reach the WebUI over loopback after gating SSO+ABAC. `hermes-personal` is that shape (`alb_target_container="policy-proxy"`). `hermes-slack` is NOT: `alb_target_container="hermes"` means the ALB targets the hermes container directly on its task ENI IP, and a socket bound to `127.0.0.1` inside the container is unreachable from that IP. Docker's own `HEALTHCHECK` still passed because it uses `curl http://localhost:8787` from *inside* the container — a different network path than the ALB probe.

**Why the bug slipped in.** The env var was added to `_HERMES_CONTAINER_BASE` when the two hermes templates were factored out of a shared base. At that point both templates went through a policy proxy, so loopback binding was universally correct. When `hermes-slack` later switched to direct ALB → hermes targeting (the Slack template intentionally exposes the WebUI so users can sign in — no policy proxy), the env var in the shared base stopped being a constant, but the comment still called it one.

**Fix.** Removed `HERMES_WEBUI_HOST` from `_HERMES_CONTAINER_BASE.environment` and reintroduced it only inside `HERMES_PERSONAL_TEMPLATE`'s hermes container entry (via `"environment": {**_HERMES_CONTAINER_BASE["environment"], "HERMES_WEBUI_HOST": "127.0.0.1"}`). `HERMES_SLACK_TEMPLATE` inherits the base without the override, so the WebUI falls back to the upstream image default of `0.0.0.0` and the ALB health check on the task ENI succeeds. Re-deployed after re-seeding — target flipped `healthy` within the grace period, CFN stack went `CREATE_COMPLETE`, deployment ended `succeeded` at `https://hermes-slack-test.chsandbox.com`.

**Test fix-up.** `test_bedrock_platform_capabilities.test_hermes_templates_enable_docker_backed_tools` had baked the old invariant in (`assertEqual(hermes_container["environment"]["HERMES_WEBUI_HOST"], "127.0.0.1")` for both templates). Changed it to branch on `alb_target_container`: expect `127.0.0.1` only when it's `"policy-proxy"`, and assert the key is absent otherwise. That keeps the test honest about the new per-template rule.

**Diagnostic path that worked.** ECS service events + target group health check, not CFN events. `describe-stack-events` stalled at `EcsService … CREATE_IN_PROGRESS (Resource creation Initiated)` with no further detail — CFN just waits for the service to stabilize. `describe-services` showed the replacement loop and `describe-target-health` surfaced `Target.FailedHealthChecks` with the container's ENI IP + port, which was the fingerprint of a loopback bind. Worth remembering: a task running + healthy inside (Docker HEALTHCHECK passing) + an ALB target-group unhealthy = bind-address mismatch.

**Cleanup wrinkle.** The first (broken) deployment couldn't be torn down via `doh_control teardown-app --remove-app` because the worker refused: `"Cannot tear down: deployment in progress (default=deploying)"`. The deployment couldn't finish because ECS couldn't stabilize the service inside the CFN stack. Manually deleted the `doh-default-hermes-slack-test-app` CFN stack in the customer account (assume-role into `266117665083`, `aws cloudformation delete-stack`), which let the worker mark the deployment `failed`, which let the removal job run to completion. Not a bug per se — the block on "deployment in progress" is correct for most cases — but the only escape hatch for a stuck-in-the-CFN-progress state is to kill the stack from the AWS side.

**Key points:**
- `HERMES_WEBUI_HOST` is per-template, not a platform constant: pin to `127.0.0.1` only when a policy proxy fronts the task; leave unset (upstream default `0.0.0.0`) when the ALB targets hermes directly.
- ALB target-group health check runs against the container's task ENI IP — a loopback-bound socket is invisible to it, regardless of whether Docker's internal HEALTHCHECK passes.
- Symptom pattern "container healthy internally + ALB target unhealthy + ECS task replacement loop + CFN stuck `CREATE_IN_PROGRESS` on the ECS service" ≈ bind-address mismatch. Check the target group, not the logs.
- Stuck `CREATE_IN_PROGRESS` CFN stacks make the deployment un-tearable via the normal worker; breaking out requires deleting the stack directly in the customer account.
- When a test asserts a "constant" env var on a shared container base, re-check the assertion when any template using that base starts diverging in its ALB-target topology.

## 2026-04-25 23:57 - [Deployment] Rename "sidecar" → "policy proxy" and fold it into the containers list

**Conversation:** [2026-04-26-0007-dc0f4fb1.md](conversations/2026-04-26-0007-dc0f4fb1.md)

Deferred cleanup from the multi-container AppTemplate refactor (see 2026-04-22 22:07 entry) finally landed. The auth/ABAC proxy stopped being a hardcoded `if sidecar_enabled:` branch tacked on after the container loop in `deploy_app.py` and became a first-class container entry with `image_source="policy_proxy"`. At the same time, the platform-owned concept was renamed from "sidecar" to "policy proxy" — the old name was overloaded (DinD and sidecar-mcp are also sidecars in the sibling-container sense) and underspecified (the proxy gates both SSO and PDP/ABAC, so "policy proxy" covers both halves while "auth sidecar" or "pdp sidecar" each undersell it). Greenfield, no prod data, so we edited migrations 0038/0039 in place rather than stacking a rename migration; plan is to tear down and recreate the pre-beta env rather than chase drift.

**Why a new `image_source` value instead of reusing `prebuilt` with a `role` discriminator.** The policy proxy has properties no other container type has: its image URI isn't user-supplied but resolved at deploy time from a per-env DOH-managed ECR repo (`doh/{env}/policy-proxy:{POLICY_PROXY_IMAGE_VERSION}`), and its *presence* in a task triggers env-level side effects (auth Lambda stack, per-env secrets, sidecar ECR stack). Reusing `prebuilt` + a role marker would force every prebuilt branch in `deploy_app.py` to re-check the role; a distinct `image_source` makes the specialization visible at the type level and keeps the image-URI resolver a single `if/elif` chain. The new ContainerConfig field `upstream_container` (only meaningful when `image_source == "policy_proxy"`) names which sibling the proxy fronts — resolved at deploy time into the `DOH_UPSTREAM_HOST=127.0.0.1` + `DOH_UPSTREAM_PORT={upstream.container_port}` env var overlay on the proxy.

**How the deploy_app.py branch collapsed.** The old flow ran the main container loop over `app_config.containers`, then appended a third container with a fully separate `task_definition.add_container(...)` call in an `if sidecar_enabled:` block, then overrode `target_group.add_target(...)` to point at the proxy container name. Now: the policy-proxy container is in the list from the start, `alb_target_container` already names it, and the main loop handles it uniformly. The one specialization is a small env/secrets *overlay* computed once up front (from `app_config.policy_proxy_container()` + its `upstream_container`) and merged into the container's env+secrets when `c.image_source == "policy_proxy"`. The `target_group.add_target` and ALB health-check-path branches (`/__policy_proxy/healthz` vs `alb_target.health_check_path`) remain — they still need to know whether a policy proxy is present — but they key on `policy_proxy is not None` rather than on a boolean flag that was orthogonal to the containers list.

**One-time secondary fix the test suite caught.** `template_deploy_service._alb_target_container()` was used for two jobs: naming the ALB target, and supplying the App row's single-container identity/build fields (`dockerfile_path`, `container_port`, etc.) from the template. Those coincided when the ALB target was always the dockerfile-built app. With a policy-proxy-fronted template, the ALB target is now the platform-owned proxy (image_source=policy_proxy, no dockerfile_path). Added `_primary_build_container()` that walks `upstream_container` when the ALB target is a policy proxy; the App row's primary fields come from there instead. Without this, `deploy_from_template` raised `"primary build container 'policy-proxy' must be image_source=dockerfile; got policy_proxy"` on the PA template.

**Scope of the rename across real AWS resource names.** Internals that ship to customer AWS (ECR repo name, Secrets Manager entry names, shared-secrets field name, CloudFormation stack IDs, health-check path, container name suffix) were renamed too — not just code symbols. Greenfield made this safe; in prod it would have required a migration story per resource. Concretely: `doh/{env}/sidecar` → `doh/{env}/policy-proxy`, `devopshero/{env}/sidecar-auth-config` → `devopshero/{env}/policy-proxy-auth-config`, `DOH_SIDECAR_TOKEN` → `DOH_POLICY_PROXY_TOKEN`, `DOH_SIDECAR_AUTH_CONFIG_SECRET_ARN` → `DOH_POLICY_PROXY_AUTH_CONFIG_SECRET_ARN`, `{app_slug}-sidecar` → `{app_slug}-policy-proxy`, `/__sidecar/healthz` → `/__policy_proxy/healthz`, `devopshero-{env}-sidecar-ecr` → `devopshero-{env}-policy-proxy-ecr`.

**What did NOT move.** The `AuthLambdaStack` keeps its name — "auth lambda" is still accurate (OIDC callback + JWT minting), and it's used *by* the policy proxy rather than being part of it. `lambdas/pdp_mock/` stays — it mocks a PDP, not a sidecar. Journal entries and `docs/conversations/*` — historical record, rewriting would be lossy and misleading (the work described there happened under the old name). Generic "sibling container" uses of the word "sidecar" in CDK comments (e.g. "sibling containers (sidecars, MCP servers, etc.) have no database contract") — correct usage of the industry term, independent of our platform feature.

**Migration editing in place vs. stacking a rename migration.** User was explicit that this is greenfield, no apps running, no need to preserve compatibility. That unlocks editing 0038/0039 in place (dropped the `sidecar_enabled` field from 0038 entirely, renamed `SidecarToken` → `PolicyProxyToken` + `sidecar_token` related_name in 0039). The alternative — stacking a new `0048_rename_sidecar_to_policy_proxy.py` — would have carried the old name forward in the migration graph forever and required a `RenameField` + `RenameModel` sequence, for no actual benefit since there's no real DB to migrate. For a dev DB that already applied the original 0038/0039, `migrate devopshero_app zero && migrate` reseats the graph cleanly. Pre-beta env teardown + recreate is the plan for the AWS side.

**Tests.** 362 passing total after the refactor: 314 Django (management commands, PDP endpoint, ABAC engine, compute modes, Bedrock capabilities, template deploy, env secrets), 27 policy_proxy unit tests, 13 auth-lambda tests, 8 pdp-mock tests. One real bug surfaced (the `_primary_build_container` issue above); the rest were fixture updates for the renamed identifiers.

**Deferred.** Nothing functional. Bucket of minor polish items (the `AuthLambdaStack` rename is deliberately skipped; some `"sidecar"` strings in `hermes_agent_backup/` are untouched because it's a backup dir).

## 2026-04-25 19:20 - [DevEx] `doh_control teardown-app --remove-app`: one-shot CLI parity with the "Remove App" UI flow

**Conversation:** [2026-04-25-1921-013aa263.md](conversations/2026-04-25-1921-013aa263.md)

`doh_control teardown-app` could only set the *latest* deployment to `TEARDOWN_PENDING` and walk away. The UI has a follow-up step that the CLI was missing: after every deployment is torn down, a "Remove App" button appears and opens a modal with three checkboxes (delete Secrets Manager entries, delete EFS app data, delete policies tagged with the app's slug); clicking it queues an `AppRemovalJob` that does the cleanup and the cascade-delete of the App row. The CLI now mirrors that with four new optional flags: `--remove-app`, `--delete-secrets`, `--delete-efs-data`, `--delete-policies`.

**Initial design (rejected): poll on `TEARDOWN_PENDING`.** First sketch was: the CLI sets every live deployment to `TEARDOWN_PENDING` and polls until they reach `TORN_DOWN` (or any → `FAILED`), then queues the `AppRemovalJob`. Then I moved the polling into the worker — let the executor flip statuses and poll, while the CLI just queues and returns. Both versions required inventing a polling cadence (5s? 10s?) and a timeout (30 min?), plus reasoning about thread-pool starvation: the polling thread sits idle waiting for *another* worker thread to claim the teardown.

**The simplification (accepted): call `run_teardown` inline.** Discarded both polling approaches once we noticed `app_deployment_teardown_executor.run_teardown(deployment_id)` is already a clean, synchronous entry point — it loads the deployment, flips it to `TEARING_DOWN`, calls the CDK teardown, sets `TORN_DOWN`/`FAILED`, returns `bool`. So the removal executor just calls it directly per live deployment. No flag-flipping, no polling, no timeout, no thread-pool starvation, and no race window where the per-deployment teardown worker and the removal executor might both target the same row.

**The actual mechanism.** Added a single boolean `teardown_first` field on `AppRemovalJob` (migration 0047). When the executor runs and finds live deployments:
- `teardown_first=False` → today's behavior (fail with "App became live again"). The UI's "Remove App" button keeps creating jobs with `teardown_first=False` because the modal is gated on `not app_is_live(app)` anyway.
- `teardown_first=True` → walk the live-deployment list serially, call `run_teardown(deployment_id=...)` per deployment, bail on the first failure. Then proceed with the existing EFS / secrets / policies cleanup and `app.delete()`.

The executor refuses to start tearing down anything if any live deployment is in an in-flight state (`PENDING`, `BUILDING`, `PUSHING`, `DEPLOYING`, `STARTING`, `TEARING_DOWN`) — that would race the deploy/teardown workers. The user is told to wait for the in-flight deploy to settle.

**CLI surface.**
- Without `--remove-app`: behaves exactly as before. The three `--delete-*` flags are rejected at parse time with a clear message — they're useless without `--remove-app`.
- With `--remove-app`: skip the latest-deployment teardown logic entirely. Create `AppRemovalJob(teardown_first=True, delete_secrets=…, delete_efs_data=…, delete_policies=…)`, flip `app.status = PENDING_REMOVAL`, return immediately. The worker handles the whole teardown-then-remove pipeline. The `PENDING_REMOVAL` status already blocks redeploys (per existing view guards), so there's no race window where the user could re-deploy under us.

**Key points:**

- The user pushed back twice on overengineering: first on per-CLI polling, then on the worker-side polling. Both pushbacks led to a smaller, sturdier design. The fact that `run_teardown` was already a clean entry point is what made inline-calls workable — kudos to whoever wrote it that way.
- "Refactor `_find_live_deployments` to also return the deployment id" was a tiny shape change but the unlock for everything downstream — without the id we couldn't have called `run_teardown` directly.
- Default `teardown_first=False` keeps the UI's `app_remove` view *exactly* as-is. That was deliberate: the UI's "Remove App" is gated on the app already being not-live, so it never needs the new behavior.
- The serial loop over live deployments is fine for now; most apps live in one or two envs. If we ever see apps deployed to many envs at once, we can switch to a thread pool, but premature.
- We didn't add a parallel `AppTeardownAndRemovalJob` model — extending `AppRemovalJob` with one bool is much less surface area than a second model with overlapping fields.

## 2026-04-25 19:22 - [Deployment] Drop DinD unix socket bind

The dind container was binding two dockerd listeners: `unix:///var/run/docker.sock` and `tcp://127.0.0.1:2375`. Nothing in the task actually talked to the socket — the hermes sidecar reaches dockerd via `DOCKER_HOST=tcp://127.0.0.1:2375`, and the only unix-socket consumer was dind's own healthcheck (`docker info >/dev/null 2>&1` defaults to the socket path). Dropped the unix bind from the dind CMD and switched the healthcheck to `docker -H tcp://127.0.0.1:2375 info`. Same failure signal, smaller attack surface: no socket file sitting on the writable layer, no redundant listener, one path in.

**Key points:**

- Two listeners, one consumer is worse than one listener: the unused bind was "security theater going the wrong way" — privilege without corresponding use. Dropping it shrinks the things that can go wrong without losing any capability.
- Healthcheck still runs inside the dind container, but explicitly via tcp. Keeps the check end-to-end (if the tcp listener ever fails to bind, the healthcheck also fails), which is what we actually want.
- Verified on `hermes-vmendi01`: post-deploy, `/var/run/docker.sock` does not exist inside the dind container, and `docker -H tcp://127.0.0.1:2375 info` reports `ServerVersion: 26.1.0`. Hermes tool calls work unchanged (same DOCKER_HOST).

## 2026-04-25 19:11 - [DomainModel] Split container env vars: `environment` (platform constants) vs `configurable_variables` (deploy-time inputs)

Template containers had one list for all env vars — `runtime_variables` — each entry carrying a 9-field shape (`required`, `auto_generate`, `default_value`, `value`, `user_editable`, `allow_empty_value`, `description`, `group`, `category`). That shape is designed for deploy-form rendering and per-deployment variation. But a growing fraction of the entries were pure platform constants the operator never touches: `DOCKER_HOST=tcp://127.0.0.1:2375`, `HERMES_WEBUI_HOST=127.0.0.1`, `DOCKER_TLS_CERTDIR=""`. For those, 8 of the 9 fields were noise — `user_editable: False` was doing load-bearing work to hide them from the form, and we carried the entire deploy-time machinery (blueprint snapshot, override merge, required-field validation) for values that don't change deployment-to-deployment.

Split the shape: each container now has an `environment: dict[str, str]` for DOH-managed constants and a `configurable_variables: list[dict]` for everything that needs per-deployment decision-making. Renamed `runtime_variables → configurable_variables` across models, services, view, template seeds, and tests. `environment` is rendered directly into `ContainerConfig.environment_variables` as `{name, value}` pairs; `configurable_variables` still goes through the materialization + blueprint-snapshot + override pipeline. The `user_editable` flag stays, because a future `configurable_variables` entry might want to be shown read-only on the deploy form (this isn't modeled yet, but the hook is preserved).

**Naming.** `environment` is the AWS/Docker term for "container env var spec" — short, honest, and its contrast with `configurable_variables` makes the intent obvious at a glance. `configurable_variables` describes what these entries *are*: values that need a decision at deploy time (either from the operator, from env shared-secrets, or from auto-generation). `runtime_variables` was vague — every env var is a runtime variable.

**Precedence in the merged env.** Three sources flow into `ContainerConfig.environment_variables`, low → high:
1. `template.environment` (platform constants, never overridable)
2. `template.configurable_variables` (materialized via `_materialize_environment_variables`)
3. `blueprint.environment_variables` (snapshot of #2 taken when the blueprint was created, possibly reflecting operator overrides)

Put differently: platform constants can't be overridden by blueprint or form input — if you need the value to vary, move it to `configurable_variables` on purpose.

**Hermes templates post-refactor.** Hermes containers now carry a three-entry `environment` dict for `DOCKER_HOST` and `HERMES_WEBUI_HOST`. DinD container carries `{"DOCKER_TLS_CERTDIR": ""}`. `configurable_variables` holds only the user-facing knobs (LLM provider/model, API keys, Slack credentials, WebUI password). Net: ~40 lines of boilerplate deleted from the seeds; the "knobs" vs "wiring" distinction is visible at the schema level.

**Ops note.** `configurable_variables` comes from the `AppTemplate` row in the DB; re-seed (`seed_app_templates`) after any in-code template change or the job worker will hit `KeyError`/stale values at deploy time. `template.configurable_variables` is read fresh per deploy via the app_config_builder; `blueprint.environment_variables` is the frozen snapshot — deliberate, so operator overrides are immutable once the blueprint is created.

**Key points:**

- `user_editable: False` was always a load-bearing lie for platform constants: the field existed to be ignored, and the deploy-form code had to iterate and skip. Splitting the schema honestly removes the lie and shrinks the form's walk.
- Separating platform wiring from deploy-time inputs at the schema layer means every `configurable_variables` entry is, by construction, a thing that *could* appear on the form. `user_editable` is now a narrower signal ("show this on the form") rather than an escape hatch.
- Precedence rule (platform < template-material < blueprint) mirrors how ECS tasks resolve env vars in practice. Platform constants going first and blueprint overrides going last is intuitive — rare that anyone expects a platform-constant to be overridable.
- Tests caught the Hermes-template shape change cleanly: one `assertEqual` on `runtime_vars["DOCKER_HOST"]["value"]` had to move to `hermes_container["environment"]["DOCKER_HOST"]`, and passed on the first try.
- Verified on `hermes-vmendi01`: after reseeding and queueing a fresh deployment, `env` inside the hermes container shows `DOCKER_HOST=tcp://127.0.0.1:2375` and `HERMES_WEBUI_HOST=127.0.0.1`, both sourced from the new `environment` dict rather than configurable_variables.

## 2026-04-25 18:56 - [Deployment] Drop DOH_HERMES_REQUIRE_DOCKER dead-weight flag

Small cleanup: both Hermes templates always set `DOH_HERMES_REQUIRE_DOCKER=1` and the entrypoint's checks around that env var are redundant with the ECS `depends_on: {docker-dind, HEALTHY}` contract that already gates hermes on a healthy DinD. Removed the env var from both templates, removed the two `elif` fallbacks from `entrypoint.sh`, and tightened the probes into unconditional fatal checks — if `DOCKER_HOST` is unset or unreachable, or `/workspace` isn't mounted, boot fails loudly instead of silently dropping to `backend: local`.

Previously the control flow read as "if docker is reachable and workspace is there, great; otherwise, if REQUIRE_DOCKER=1, fail; otherwise, run without tools." In practice REQUIRE_DOCKER was always `1`, so the "otherwise" branches were dead. Deleting them also kills the only place in the codebase where Hermes would have silently started without tool access, which would have been confusing to debug if it ever actually happened.

**Key points:**

- `depends_on: HEALTHY` on the hermes container is the primary contract; duplicating it with a per-container env-var check just creates two sources of truth that can drift. The cdk side is the one that actually blocks the task from reaching RUNNING when DinD is unhealthy; the entrypoint check now only covers the degenerate case where someone deploys Hermes without the DinD sidecar at all.
- "Silent degraded mode" is a bad default for an AI agent: a Hermes instance without tools looks identical from the outside to one with tools until the user asks it to do something. Better to fail startup than to fail the first tool call.

## 2026-04-25 18:45 - [Deployment] Sibling EFS mounts: hermes home and workspace as peers, not nested

**Conversation:** [2026-04-25-1845-afc23e32.md](conversations/2026-04-25-1845-afc23e32.md)

Follow-up to the DinD fix from earlier today. That fix bound the DinD workspace access point to two container paths (`/workspace` and `/home/hermeswebui/.hermes/workspace`) so the agent would hit EFS regardless of which name it used. It worked, but the real mistake was the nested layout itself — workspace-as-a-subdir-of-the-agent-home. This entry flattens that into two sibling access points on EFS, mounted independently in the hermes container, with DinD taking only the workspace one.

**Old shape.** One `EfsConfig` with `mount_path` (the whole deployment root), `posix_uid`/`posix_gid` shared across containers, and an optional `docker_workspace_subpath`. CDK created a root access point at `/deployments/<app>/` plus a second access point at `/deployments/<app>/workspace/`. The hermes container mounted both — root → `/home/hermeswebui/.hermes`, and the subpath → `/home/hermeswebui/.hermes/workspace` overlaid on top. DinD mounted only the subpath at its own `/workspace`. Per-container opt-in lived in two flags (`efs_mount: bool`, `efs_docker_workspace_only: bool`) plus a `efs_docker_workspace_container_path` override. Workable, but every name in it (`*_only`, `subpath`) was a relic of the nested model, and the branching in `deploy_app.py` reflected that — two mutually exclusive `if/elif` arms per container.

**New shape.** `EfsConfig` carries a list of named `EfsMount`s, each with its own `name`, `subpath`, `container_path`, and POSIX ownership. Containers opt in with a single field `efs_mounts: list[str]` naming the mounts they want. CDK iterates the mounts list to create N access points + N task-level volumes, then per container iterates `efs_mounts` to emit the matching `MountPoint`s. The "main vs exception" asymmetry is gone; there's one code path that handles 0/1/N mounts uniformly.

**Concrete layout for Hermes:**

- EFS: `/deployments/<app>/hermes/` and `/deployments/<app>/workspace/` as siblings.
- hermes container: `hermes` mount at `/home/hermeswebui/.hermes`, `workspace` mount at top-level `/workspace`. Agent home and workspace are now independent paths; removing one doesn't affect the other.
- docker-dind sidecar: `workspace` mount at `/workspace`, nothing else. DinD never sees Hermes config, memory, skills, or the venv — even if an attacker escaped a tool container, the agent-home blast radius is closed off at the mount boundary.

**Downstream simplifications from the flat layout:**

- `template_repos/hermes_agent/Dockerfile`: dropped the `rm -rf /workspace && ln -s /home/hermeswebui/.hermes/workspace /workspace` symlink. `/workspace` is a real mount now; `HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace` points at it directly.
- `entrypoint.sh`: `docker_volumes` collapses to a single `["/workspace:/workspace"]` bind. The per-boot Python block that surgically rewrote the `terminal.*` section of `config.yaml` on every boot is **deleted** — it was a workaround for the earlier two-path binding and is no longer necessary. First-boot sed is enough.
- `deploy_app.py`: the `if c.efs_docker_workspace_only: ... elif efs_access_point and c.efs_mount: ... + nested second mount ...` branch collapses into a single `for mount_name in c.efs_mounts:` loop. Access point creation symmetrizes to one loop as well.

**Migration.** Destroyed and redeployed `hermes-vmendi01` from scratch instead of relocating agent-home contents on EFS. Tests (314) pass. Post-deploy verification from inside the running hermes container: `/workspace` is a top-level EFS mount, `/home/hermeswebui/.hermes/` has the agent state with no `workspace` subdir, `config.yaml` shows the expected `backend: docker`, `cwd: /workspace`, `docker_volumes: ["/workspace:/workspace"]`.

**Schema shape is greenfield-compatible for future apps.** Today only Hermes uses >1 mount, but the list-of-mounts shape matches AWS's primitive 1:1 (a filesystem can back many access points, each with its own POSIX ownership and subpath). Future apps that want, say, a scratch mount + a read-only shared-library mount drop in without another schema revision.

**Operational gotcha worth remembering.** After changing the in-code template, I forgot to run `seed_app_templates` before queuing the new deployment — the stale `{mount_path: ...}` dict was still in the DB, so `app_config_builder` hit `KeyError: 'mounts'` at deploy time. `template.efs_config` is read live from the AppTemplate row, not from a blueprint snapshot, so reseed-then-retry was enough. The retry creates a *new* Deployment row (not a state flip on the old one); easy to miss when polling by UUID.

**Key points:**

- Sibling mounts > nested mounts, even when the data on disk is identical. The container-side path layout is what the agent learns from `HERMES_WEBUI_DEFAULT_WORKSPACE` and from `pwd`; making it flat removes an entire class of "agent wrote to path X but it's not where I expected" bugs at the mount layer rather than papering them over at the bind layer.
- The refactor deletes more code than it adds. Every place the old shape had a special case (CDK, entrypoint, Dockerfile) collapses into a uniform loop or a single bind. The one place it gets slightly more verbose is the seed template (dict-with-list instead of flat dict), which is honest about the fact that there are two mounts.
- `efs_mounts: list[str]` is the right granularity for per-container opt-in. Boolean `efs_mount` conflates "which mounts" with "any mount," and any future "mount X but not Y" case breaks it. Named mounts scale to any mix.
- Per-mount POSIX ownership is part of the shape even though Hermes uses 1024:1024 for both mounts today. Future apps where the tool sidecar wants root-owned workspace but the app container wants UID-owned home don't need another refactor.
- **Open question I flagged but did not act on:** the whole `terminal.backend: docker` mechanism (DinD sidecar, privileged EC2, DOCKER_HOST plumbing, `docker-dind` in the template, extra cross-container dependency) exists to give each Hermes tool call a fresh ephemeral container. But Hermes-personal is one user per deployment and Hermes-slack is one org — there's no multi-tenancy to protect. If we moved to `backend: local` (run tools as subprocesses in the hermes container, bake Python+Node into the image), we'd delete the entire DinD container, the `depends_on` chain, `privileged: True`, the EC2-only constraint (goes back to Fargate), the `dockerd`-prepend CMD hack, the DOCKER_HOST probing in entrypoint, and ~200MB of idle memory. The trade is per-command disposability, which we don't currently exercise. Worth its own experiment; deferred.

## 2026-04-25 17:52 - [Bugfix] Hermes agent writes to overlay instead of EFS with terminal.backend=docker

**Conversation:** [2026-04-25-1752-afc23e32.md](conversations/2026-04-25-1752-afc23e32.md)

The Docker-backed terminal on Hermes Personal looked like it was working — the agent ran `mkdir -p /home/hermeswebui/.hermes/workspace && touch .../testing && ls -la`, saw the file, and reported success. But `doh_efs_browse /efs/deployments/hermes-vmendi01/workspace` came back empty. The agent was writing into the tool container's ephemeral overlay and lying to itself about persistence.

The Hermes task has three relevant mount paths that are easy to confuse:

- On the `hermes` parent container, `/home/hermeswebui/.hermes` is bound to the app EFS access point, and `/home/hermeswebui/.hermes/workspace` is a subpath access point on the same filesystem.
- On the `docker-dind` sidecar, only the workspace subpath is mounted, at `/workspace`. DinD has no view of the rest of the `.hermes` tree.
- When Hermes uses `terminal.backend: docker`, it calls `docker run` against the DinD sidecar, which spins up a fresh `nikolaik/python-nodejs:...` tool container. That tool container is a separate image with its own filesystem; it only sees what we bind in via `docker_volumes`.

The seeded config had `docker_volumes: ["/workspace:/workspace"]`, so `/workspace` in the tool container pointed at EFS, but `/home/hermeswebui/.hermes/workspace` did not exist at all. The agent happily reused the `HERMES_WEBUI_DEFAULT_WORKSPACE` path it had learned from its parent container's env — which lives only in the tool container's writable overlay and vanishes on container exit.

**Primary fix.** In `template_repos/hermes_agent/entrypoint.sh`, bind the DinD `/workspace` to both container paths on every tool run:

```
DOCKER_VOLUMES='["/workspace:/workspace", "/workspace:/home/hermeswebui/.hermes/workspace"]'
```

Now either path name the agent reaches for lands on the same EFS-backed data, and the outside-world view via `doh_efs_browse` matches what the agent sees.

**Secondary fix — first-boot config.yaml trap.** The entrypoint only generates `config.yaml` from the template on first boot (`if [ ! -f "$HERMES_DIR/config.yaml" ]`) so user edits on EFS are preserved. That's correct for user-owned sections, but `terminal.*` is strictly DOH-controlled: backend, cwd, and `docker_volumes` are all derived from the task's mount layout, not user preference. Without a rewrite on boot, already-deployed apps on existing EFS volumes would never pick up the fix unless the volume was wiped.

Added a small Python block to the entrypoint that, on every boot, surgically rewrites only the `terminal:` block of `config.yaml` — leaving every other top-level key byte-for-byte intact. Tested locally against the template to confirm the rewrite is scoped and idempotent.

**Collateral fix — DinD port collision blocked the redeploy.** The moment I triggered a redeploy to pick up the entrypoint change, every new task failed with `failed to load listeners: listen tcp 127.0.0.1:2375: bind: address already in use`. ECS circuit-breaker-rolled-back the deployment three times before I stopped it. Task definition diff between `:7` (working, currently running) and `:8` (new, failing) showed a single change: `command` went from `[]` to `["--host=unix:///var/run/docker.sock", "--host=tcp://127.0.0.1:2375"]`.

Root cause: the `docker:26.1.0-dind` image's `dockerd-entrypoint.sh` checks the first CMD arg. If it starts with `-`, it interprets it as a dockerd flag and **prepends** its own defaults — including `--host=tcp://0.0.0.0:2375`. So our loopback bind was appended on top of the entrypoint's all-interfaces bind, and both fought over port 2375. The stock image works because its default CMD injects the `--host=tcp://0.0.0.0:2375` exactly once.

Fix: prepend `"dockerd"` as the first CMD arg in `seed_app_templates.py`. The dind entrypoint then treats the rest as pure args with no default injection, and our two `--host` flags are the only binds. Reseeded the template and the retry succeeded.

**Verification.** Post-fix `config.yaml` on the running container now reads:

```
terminal:
  backend: docker
  cwd: /workspace
  docker_volumes: ["/workspace:/workspace", "/workspace:/home/hermeswebui/.hermes/workspace"]
```

User manually tested a file write from the Hermes UI against both paths and confirmed the file shows up under `doh_efs_browse /efs/deployments/hermes-vmendi01/workspace`.

**Key points:**

- The DinD tool-container split makes it very easy to write to a path that looks right but isn't on EFS — there's no error and no warning, just a silent overlay write that disappears. The two-path bind makes the failure mode impossible rather than relying on the agent picking the blessed path.
- `config.yaml` is semi-user-owned on EFS, so "first-boot only" is correct for most sections. But `terminal.*` is DOH plumbing, not user config — it needs to be rewritten on every boot so fixes propagate without a fresh volume. Scope the rewrite tightly (one top-level block) so user-owned sections are preserved.
- `docker:dind` + custom `command` is a sharp edge. If the first arg starts with `-`, `dockerd-entrypoint.sh` prepends its defaults; if it's `dockerd` (or any non-flag token), it doesn't. Always start with `dockerd` when overriding CMD. Documented inline in the template seed.
- The existing AppTemplate seed edits were already in-flight on this branch (not committed), and the command shape was wrong before I got here — but nothing exercised it until a new task definition revision shipped. "Works on the live task" is not the same as "task def works from scratch," and we had no coverage that would have caught this; a `doh_raw --synth-only` of a Hermes template synth plus a real task-def diff would have flagged the change in command shape before rollout.
- Debugging trail worth remembering: service events → circuit breaker; `describe-tasks` → `docker-dind exitCode=1`; log streams list → failed tasks only have `docker-dind` streams (hermes never started because dind was `essential=True` and crashed); dind log tail → "bind: address already in use"; task def diff → single-line command change. Four steps, each one narrowing the surface.

## 2026-04-25 16:45 - [Bugfix] Sidecar proxy disconnects during long idle streams

**Conversation:** [2026-04-25-1645-130595e3.md](conversations/2026-04-25-1645-130595e3.md)

Customer apps behind the sidecar proxy were dropping connections mid-session with a browser-side "Connection lost / reconnecting" error. The sidecar logs showed an httpx `ReadTimeout` surfacing out of starlette's `StreamingResponse`, unwinding a task group and tearing down the response. Root cause: `httpx.AsyncClient()` in `sidecar/sidecar/app.py` was constructed with default timeouts, which apply a 5s read timeout to the whole stream. Any idle gap longer than 5s between chunks — very common on LLM streams or permission-prompt streams where the user is thinking — killed the connection.

Fix was to construct the client with `httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)`. `read=None` is the one that matters: streaming responses must be allowed to go quiet without the proxy interpreting it as a dead connection. `connect`, `write`, and `pool` stay finite so an unreachable upstream or a saturated pool still fails fast. The usual concern with `read=None` — a silently dead but TCP-alive upstream hanging forever — is best addressed with TCP keepalive or an application-level heartbeat, not a read timeout; the upstream already sends SSE keepalives so we're covered.

Second gotcha: the sidecar image is built and pushed by the customer app deploy pipeline (`devopshero_app/services/infra_customer/deploy_app.py`), but tagged with a hardcoded version constant `SIDECAR_IMAGE_VERSION`. ECR push is idempotent by tag, so a redeploy without a version bump would quietly re-use the old image. Bumped the constant to `0.1.1` so the next redeploy of each affected app picks up the fix.

**Key points:**

- `httpx.AsyncClient()` defaults apply a 5s read timeout to streams; for a streaming proxy this is wrong, use `read=None`.
- Keep `connect`/`write`/`pool` finite — those guard against different failure modes (unreachable upstream, saturated pool) where fast failure is desirable.
- Sidecar image rollout is not automatic on redeploy; `SIDECAR_IMAGE_VERSION` in `deploy_app.py` must be bumped to force a new push, and each customer app must then be individually redeployed.
- No automatic fan-out across existing deployments — this is by design (platform-owned version), but worth remembering when shipping a sidecar fix.

## 2026-04-25 12:18 - [Deployment] Hermes EC2-backed ECS compute mode

**Conversation:** [2026-04-25-1218-db633dc8.md](conversations/2026-04-25-1218-db633dc8.md)

Added a second ECS compute mode so Hermes can run on EC2-backed ECS capacity as well as the existing Fargate path. The design keeps one ECS cluster per DOH environment and adds an EC2 Auto Scaling Group capacity provider to that cluster. `DeploymentBlueprint.compute_mode` is the per-app/environment selection, `AppTemplate.default_compute_mode` supplies the deploy-form default, and `AppConfig.compute_mode` carries the choice into CDK. Existing apps default to Fargate; Hermes Personal and Hermes Slack now default to EC2 capacity.

The app deploy path now branches at the ECS task/service layer. Fargate deployments keep the existing `FargateTaskDefinition` + `FargateService` path. EC2 deployments use an EC2-compatible task definition with `awsvpc` networking and an `Ec2Service` capacity-provider strategy targeting `devopshero-{env_slug}-ec2-capacity`. We deliberately kept the task networking model the same: private subnets, IP target groups, the existing app security group, EFS access points, task roles, auth sidecar, and localhost sibling-container communication all continue to work the same way.

The environment base stack creates a dedicated ECS container-instance role, security group, Launch Template, ASG, and ECS ASG capacity provider. The instance type is currently hardcoded as `m8g.large` (ARM64 Graviton4) so it matches the existing ARM64 image build/runtime target. The ASG has `min_capacity=0` and no explicit desired capacity; this keeps idle EC2 instance cost at zero while avoiding CDK's warning that every deploy would reset the ASG size. ECS managed scaling is responsible for scaling instances once EC2-backed services need capacity.

Two deployment-time lessons came out of testing against Humanity Rules Sandbox. First, the direct `doh_raw --base` command still called the old `deploy_base.deploy` signature and did not pass `shared_alb_hosted_zone`; normal environment provisioning had the right path, but the raw operator command needed to be updated. Second, CDK's default `AutoScalingGroup` synthesis used a legacy Launch Configuration, and the account rejected it with: `The Launch Configuration creation operation is not available in your account. Use launch templates to create configuration templates for your Auto Scaling groups.` The fix was to create an explicit EC2 Launch Template and pass mutable Linux user-data into it so CDK still injects the `ECS_CLUSTER=...` registration line.

`doh_app_shell` also needed to catch up with template-backed container naming. The old command assumed the ECS container name was exactly the app slug. New template-backed tasks name containers as `{app_slug}-{template_container_name}`, so the command now defaults to the sole template container when there is one, or to `source_template.alb_target_container` for multi-container templates. Operators can override with `--container hermes`, `--container sidecar-mcp`, or `--container sidecar`; full ECS container names still pass through. I initially added explicit `ssmmessages:*Channel` grants to the app task role for ECS Exec, but removed them after confirming `doh_app_shell` worked against an app that had not been redeployed. The container-name resolution was the real compatibility issue, and broadening task-role IAM was unnecessary.

**Key points:**

- EC2 capacity is environment infrastructure, not app infrastructure. The base stack must be redeployed before an EC2-mode app deploy can work; app deploys preflight that the capacity provider is associated with the cluster and fail clearly if not.
- Fargate remains the compatibility default for existing rows and non-Hermes templates. Hermes defaults to EC2 because long-lived agents are the first concrete use case for customer-owned compute, but the deploy form still lets operators choose Fargate.
- The ASG can exist with zero EC2 instances. Seeing no container instances while no EC2-backed service is running is expected; the ASG and capacity provider are the resources that prove the base stack updated.
- `m8g.large` costs roughly `$0.0898/hour` in `us-east-1`, about `$66/month` if one instance runs 24/7. With `min_capacity=0`, idle instance cost is zero; always-on Hermes on EC2 should be budgeted around one running host unless tasks bin-pack onto an existing instance.
- Tests now synthesize both compute modes, assert the EC2 path uses a Launch Template rather than a Launch Configuration, verify compute-mode propagation from template/blueprint into `AppConfig`, and cover `doh_app_shell` container-name resolution.

## 2026-04-24 12:39 - [Platform] Hermes Bedrock access moved from static keys to ECS task role

Hermes no longer needs static Bedrock access keys. The current codebase had conflicting signals: `template_repos/hermes_agent/entrypoint.sh` already documented that Bedrock deploys should rely on ECS task-role credentials, but the runtime still hard-failed unless `AWS_BEDROCK_ACCESS_KEY_ID` and `AWS_BEDROCK_SECRET_ACCESS_KEY` were injected as app secrets. Meanwhile the per-app ECS task role created by `deploy_app.py` only granted platform plumbing permissions (Secrets Manager, EFS, database, sidecar token), so removing the keys without a CDK-owned task-role grant would have broken Hermes immediately.

Added a template-level `platform_capabilities` JSON field on `AppTemplate`. This is intentionally platform-owned infrastructure metadata, separate from user-managed `doh-app-permissions`. Hermes Personal and Hermes Slack now declare `["bedrock-runtime"]`; `app_config_builder` propagates that into `AppConfig`; `deploy_app.AppStack` checks both the capability and the effective runtime config (`DOH_LLM_PROVIDER` or `DOH_AUX_PROVIDER` equals `bedrock`) before adding Bedrock grants to the ECS task role. That keeps the signal explicit while avoiding Bedrock permission on a Hermes deployment configured for OpenAI/custom.

The Bedrock grant is deliberately generous because DOH is still pre-customer and the near-term goal is to avoid expanding IAM every time a Bedrock test path hits a new API. The task role now gets `InvokeModel`, streaming invoke, token counting, guardrail apply/read/list, model/profile/provisioned-throughput read/list, prompt read/list/render, and related Bedrock runtime discovery actions on `*`. This is broader than least privilege, but it is still scoped to the per-app ECS task role and lives in CDK-owned policy rather than in the permission editor's user-managed policy.

Removed `AWS_BEDROCK_ACCESS_KEY_ID` and `AWS_BEDROCK_SECRET_ACCESS_KEY` from the Hermes seeded templates. `AWS_BEDROCK_REGION` remains config. Updated Hermes `entrypoint.sh` so Bedrock only requires the region, mirrors it into `AWS_REGION` / `AWS_DEFAULT_REGION`, derives the Bedrock runtime URL, and lets boto3/AWS SDKs get credentials from the ECS task-role credential provider. Updated the Hermes README to match.

Validation added:

- `test_bedrock_platform_capabilities.py` synthesizes the CDK `AppStack` and asserts the Bedrock task-role policy appears only when the template capability and effective Bedrock provider are both present.
- Existing multi-container projection test now verifies `platform_capabilities` reaches `AppConfig`.
- Hermes seed-template test coverage asserts the templates declare `bedrock-runtime` and no longer declare Bedrock access key secrets.

Commands run:

- `uv run manage.py test devopshero_app.tests.test_bedrock_platform_capabilities devopshero_app.tests.test_multi_container_app_config devopshero_app.tests.test_template_deploy_owner_field`
- `uv run manage.py test devopshero_app.tests.test_env_sidecar_secrets`
- `uv run manage.py makemigrations --check --dry-run`
- `git diff --check`

Operational note: deployment must run from a checkout containing the updated `template_repos/hermes_agent` because template-backed apps build from the local template repo path. After merging to `main`, update the DOH platform, run migrations, run `uv run manage.py seed_app_templates`, and redeploy Hermes apps so CloudFormation updates the ECS task role and the Docker image contains the new entrypoint.

## 2026-04-22 19:09 - [Deployment] Post-deploy hermes-slack debugging: CMD, essential, and short-slug prefill

**Conversation:** [2026-04-22-1909-a7988bb4.md](conversations/2026-04-22-1909-a7988bb4.md)

First live hermes-slack deploy after the multi-container refactor surfaced three distinct issues. Fixed in sequence; each one worth recording because the root causes were subtle enough to waste time diagnosing twice.

**1. ALB target-group name ending in a hyphen.** CDK synth failed with `Target group name: "doh-default-ai-assistant-hermes-" must not begin or end with a hyphen.` The 32-char truncation at `deploy_app.py:758` landed exactly on a hyphen boundary because the default App name derived from the template is `ai-assistant-hermes-slack`. Fix was a `.rstrip("-")` on the truncation (`doh-{env_slug}-{app_name}`[:32].rstrip("-")), which produces a 31-char `doh-default-ai-assistant-hermes`. Kept the rstrip as a defense-in-depth measure even after the longer root-cause fix below — slugs can still grow past 32 chars for other templates.

**2. sidecar-mcp container exited 0 before Hermes started → TaskFailedToStart.** Every stopped task showed the same pattern: `sidecar-mcp exitCode=0, hermes exitCode=null`. Because ECS containers are `essential=True` by default, the MCP's clean exit killed the pod before Hermes's first boot. Root cause: sidecar-mcp's `ENTRYPOINT ["node", "dist/index.js"]` with no CMD, so without the `--http` flag `src/index.ts:91` fell through to `startStdioServer()` which blocks on stdin. ECS Fargate tasks have no attached TTY, so stdin hits EOF immediately, the server exits 0, and the task is killed.

Fix required a new per-container field `command: list[str] | None` on `ContainerConfig`, plumbed through `app_config_builder.py` to `task_definition.add_container(command=…)`. The image's `ENTRYPOINT` is preserved; only the CMD is overridden. Seeded the sidecar-mcp container with `command: ["--http", "--port", "7777", "--host", "127.0.0.1"]`. This is also a **generic mechanism** — any future prebuilt-image AppTemplate where the image's default CMD doesn't match how DOH wants to run it can now set this.

Initially added a `curl -fsS http://127.0.0.1:7777/health` healthcheck too, which was wrong on three counts: (a) sidecar-mcp's HTTP transport only routes `/mcp` — `/health` would 404 every probe, (b) `node:22-slim` doesn't ship curl, and (c) coupling the MCP's liveness to the task's health via `essential=True` reintroduced the blast-radius tradeoff we'd explicitly rejected earlier. Dropped the healthcheck entirely and added another new per-container field `essential: bool = True` (defaults to ECS default), setting the sidecar-mcp container to `essential=False`. Now an MCP crash leaves the task up; Hermes gracefully degrades to a pure-LLM state, and the MCP gets restarted on the next task redeploy.

**3. Long app slug propagating into every resource name.** The third iteration surfaced the underlying issue: the deploy form's default App name came from `template.name` ("AI Assistant — Hermes (Slack)") which slugified into `ai-assistant-hermes-slack` and then fed into every derived resource — CDK stacks, ECS service, task role, ECR repos, target group, log stream prefixes, Secrets Manager paths, EFS access point, DNS subdomain. The 32-char target group limit was only the loudest collision.

We have `AppTemplate.slug` ("hermes-slack") already sitting there — short, already unique among templates, and it's the obvious source for the default App slug when no `prefill_name` pattern is set. Changed `_compute_default_app_name` in `views/template_deploy.py` to fall back to `template.slug` instead of `template.name`. One-line fix; catches every template uniformly. Templates that want a friendlier or auto-disambiguating default (like Personal Hermes's `hermes-{username}{index}`) keep setting `prefill_name`.

After this, `doh-default-hermes-slack-*` everywhere, 24-char resource prefix at worst, well clear of AWS limits. The `rstrip("-")` from fix #1 becomes mostly dead code but stays as defense-in-depth for long custom slugs.

**Key points:**

- **Per-container `command` and `essential` are reusable now.** Not just sidecar-mcp-specific — any prebuilt container whose image was built for a different runtime model (stdio CLI, foreground daemon, etc.) can override its CMD; any sidecar whose crash shouldn't kill the task can set `essential=False`. Both map 1:1 to ECS primitives.
- **Why not default `essential=False` globally?** The auth sidecar, for instance, genuinely must be up for the ALB-target container to be reachable — its crash should take the task down and trigger a restart. Per-container opt-in is right; it's a property of the integration, not a template-wide setting.
- **`prefill_name` pattern still does disambiguation.** The fallback to `template.slug` only applies when `prefill_name` is empty. Personal Hermes's `hermes-{username}{index}` still wins because it's set. Multi-instance templates should always set `prefill_name` with an `{index}` token — otherwise the second deploy collides on `App.slug` (unique per org) and errors out. Not a new concern; just worth naming.
- **ECS HEALTHCHECK vs ALB health check are not the same.** The ALB's target-group health check hits the ALB-target container's `health_check_path` over HTTP. A container-level HEALTHCHECK (from the Dockerfile or `task_definition.add_container(health_check=...)`) runs `CMD-SHELL` inside the container. For non-ALB-target containers, an ALB-style path is just cosmetic — only the container-level command runs. The sidecar-mcp `health_check_path` I'd initially seeded was doing literally nothing; removed.
- **Debugging trail that was useful.** `aws ecs describe-tasks` at the container level showed `exitCode=0` + `reason=null` on sidecar-mcp and `lastStatus=STOPPED` + `exitCode=null` on hermes. "Started and exited cleanly" next to "never started" was the tell that essentiality was killing the pod, not a crash or image-pull failure. Service events were empty because the service had `runningCount=0, desiredCount=0` after the circuit breaker rolled it back. The task-level `stoppedReason: "Task failed to start"` is generic; the container-level fields are the signal.

## 2026-04-22 12:26 - [UI] Template deploy form: per-container card in the config summary

**Conversation:** [2026-04-22-1226-a7988bb4.md](conversations/2026-04-22-1226-a7988bb4.md)

After reseeding the multi-container `hermes-slack` template and opening the deploy form, the Configuration Summary read "Containers: hermes, sidecar-mcp (prebuilt)" as a single comma-joined line. Fine for one container but useless at describing a two-container task. Replaced it with a stacked list: one card per container, each carrying its name, colored badges (`dockerfile` / `prebuilt`, `ALB target`, `EFS`), and a per-field detail list (Port, Health, and either the Dockerfile path for `dockerfile` images or the `doh/{env}/{repo}:{version}` reference for `prebuilt`).

First iteration used a 2-column `<dl>` grid for the detail rows. User wanted one row per field ("make Health just another row"), so dropped the grid for `space-y-1` vertical stacking. Matches the plain-bullet feel of the rest of the summary and avoids the awkward half-filled row that happens when a container has no health check.

**Key points:**

- The badges carry the real information density — `(prebuilt)` inline with the name was technically accurate but read as a parenthetical aside rather than a first-class signal. Moved to a colored pill and paired with a `dockerfile` pill on the sibling container so the contrast is visible even when an operator doesn't know what either term means.
- ALB target is a per-template exactly-one, so the badge only renders on the matched container. Quick visual anchor for "which of these is the ALB-facing one" — most useful when a template grows beyond two containers and the answer stops being obvious.
- `{env}` is kept as a literal placeholder in the prebuilt-image line. The template deploy form is pre-deploy; no environment is bound yet from the Configuration Summary's perspective. Swapping in the selected environment's slug once the env dropdown is picked would be nicer, but requires wiring the env selection back into the summary — deferred.
- No view change needed. `template.containers` is already passed through; the template reads it directly.

## 2026-04-22 11:47 - [DomainModel] Multi-container AppTemplates + sidecar-mcp integration for Slack Hermes

**Conversation:** [2026-04-22-1147-a7988bb4.md](conversations/2026-04-22-1147-a7988bb4.md)

Generalized DOH's AppTemplate model from "one container + optional hardcoded auth sidecar" to "an explicit list of containers per template," then used that mechanism to ship the Slack Hermes ↔ sidecar-mcp integration. Hermes now runs as a two-container ECS task: the Hermes WebUI/gateway, plus the sidecar-mcp aggregator bound to `127.0.0.1:7777`. Hermes's `config.yaml` carries a static `mcp_servers.sidecar` block pointing at that loopback address. The design landed after a long back-and-forth that rejected several more convenient options; the reasoning matters.

**Rejected options, and why.** The first-instinct shape was "deploy sidecar-mcp as its own AppTemplate" — it would reuse every existing clone-build-push-ECS path with zero new abstractions. Killed once I looked at DOH's networking: the env ALB is hardcoded `internet_facing=True, SubnetType.PUBLIC` at `deploy_base.py:277-285`, the task SG allows all intra-VPC traffic at `deploy_base.py:186-195`, there's no internal service discovery, and no per-template internal-only toggle exists. Any DOH-deployed app is public if DNS resolves. A bearer-token-gated endpoint holding GitHub/Jira/AWS/Datadog service-account tokens is a single-secret-leak → full blast radius. We also rejected stdio-child-of-Hermes (couples release cadences, bloats the image), adding internal-ALB support (bigger change than the feature warranted), and a generic "add-on template system" (premature generality; no second use case). Landed on a **sidecar-in-the-same-task** shape where loopback binding is the security boundary, and generalized DOH's AppTemplate model to express it as data rather than hardcoded branches.

**Why generalize `AppTemplate` instead of hardcoding Hermes + sidecar-mcp.** The auth sidecar is a genuinely platform-level concern (SSO/ABAC for any app that opts in); sidecar-mcp is "Hermes happens to bundle this." Giving sidecar-mcp the same hardcoded-sidecar treatment as auth would be the wrong precedent: a future integration would deserve the same privileged path, and `deploy_app.py` would accrete branches. The generalization translates the "add_container" mechanism ECS (and Docker Compose) already expose into AppTemplate data.

**Schema shape (greenfield — no data migration).** `AppTemplate` gains `containers: JSONField(default=list)` and `alb_target_container: CharField(null=True)`. Legacy per-container fields (`source_repo_path`, `dockerfile_path`, `build_strategy`, `app_type`, `container_port`, `health_check_path`, `health_check_command`, `health_check_grace_period`, `runtime_variables`) are **removed** from `AppTemplate` entirely and moved inline into each container dict. Task-level fields stay (`cpu`, `memory`, `efs_config`, `datastore_config`, `sidecar_enabled`, `default_tags`, etc.). `DeploymentBlueprint` undergoes the parallel move: the flat `environment_variables` + `app_secrets` fields are replaced by `containers: JSONField` mirroring the template shape per-container. Migration `0044_multi_container_apptemplate`.

**Per-container dict (flat, JSON-stored).** Each entry carries:
- `name` — stable identifier, referenced by `alb_target_container` and by other containers in `depends_on` (not yet used; see below).
- `image_source: "dockerfile" | "prebuilt"` — the only discriminator. `dockerfile` = DOH builds from `template_repos/<source_repo_path>` into a per-app ECR repo at deploy time; `prebuilt` = references a pre-pushed image at `doh/{env_slug}/<ecr_repo>:<version>` built out-of-band by an operator.
- Image fields (flat per discriminator): `source_repo_path`/`dockerfile_path` for dockerfile; `ecr_repo`/`version` for prebuilt.
- `container_port`, `health_check_path`, `health_check_command`, `health_check_grace_period` — per-container.
- `runtime_variables` — per-container list, same shape as today's top-level. `category: "secret"` entries feed the shared-bag; `category: "config"` become ECS env vars.
- `efs_mount: bool` — opts the container into mounting the task-level EFS volume.

**Things deliberately dropped from the per-container schema (YAGNI):**
- `depends_on`. ECS default is "start in parallel, no coupling," which matches the product decision: Hermes must start regardless of sidecar-mcp's health; the MCP client reconnects if the aggregator comes up later. Adding `depends_on` would earn no behavior today.
- Auth sidecar retrofit into the `containers` model. Stays on its own `sidecar_enabled: bool` branch and is appended on top of whatever `containers` produced. Folding it in is a clean follow-up but introduces risk that isn't worth carrying in this change.

**Secret handling — one big thing that simplified the design.** Discovered mid-conversation that `secrets_utils._resolve_secret_value` already encodes the rules we needed: literal → use as-is; `""` → inherit from env `shared-secrets`; `None` → auto-generate. No new `secret_source: "app" | "env_shared"` field needed. Multi-container just unions each container's declared secrets into the task-level `AppConfig.app_secrets` bag. Same-name fields across containers must declare **identical** values (literal/`""`/`None`); mismatch raises `ContainerSecretCollision` **before any AWS call**. The shared bag is still written to `devopshero/{env_slug}/{app_name}/secrets` at deploy-prep time; each container's `ecs.Secret.from_secrets_manager` wiring only references the fields that container declared.

**`AppConfig` restructure.** `AppConfig` is the dataclass consumed by `deploy_app.py`. It gained `containers: list[ContainerConfig]`, `alb_target_container: str | None`, and an `alb_target()` helper. Legacy `ecr_repo_name`, `container_port`, `health_check_*`, `environment_variables`, `app_source_path` moved into `ContainerConfig`. `AppConfig.app_secrets` stays as the task-level shared bag (collision-checked union). `AppConfig.app_source_path` remains task-level — all `dockerfile` containers today share the same cloned source tree; a future monorepo-multi-image case can introduce per-container subpaths.

**`deploy_app.py` rewrite.** `EcrStack` now creates **one ECR repo per `dockerfile` container**, named `doh/{env_slug}/{app_slug}-{container_name}`. Prebuilt containers don't get per-app repos — they reference a shared per-env repo built out-of-band. `AppStack` collapses the old single-container + `if sidecar_enabled:` branch into a single loop over `app_config.containers`: per-container image URI (dockerfile → `{app-ecr}:{image_tag}`; prebuilt → `doh/{env}/{repo}:{version}`), per-container env (dict built from the container's `environment_variables`), per-container secrets (projected from the task-level bag by field-name declaration), per-container log stream prefix, optional EFS mount, optional HEALTHCHECK. Only the `alb_target_container` gets a `PortMapping` — others communicate via the task's shared loopback where no mapping is required in Fargate. Database env-vars + secrets are projected into the ALB-target container only (sibling containers don't have a database contract). The auth sidecar block stays intact at the end of the method as an additive modifier that reroutes the ALB target through its listen port.

**`deploy()` orchestration got two additions.** Phase 2a: `_missing_prebuilt_images()` does `ecr.describe_images` for every prebuilt container's `{repo}:{tag}` and hard-fails with an operator-actionable error before CDK runs — naming the exact `doh_build_prebuilt_image` invocation to run. A missing prebuilt image would otherwise only surface hours later as an ECS pull error. Phase 2b loops the dockerfile containers and builds + pushes each image separately via `ecr_utils.build_and_push_docker_image`. Teardown (via the teardown executor's synthetic AppConfig) reconstructs a minimal containers list from the template so every per-app ECR repo gets emptied; prebuilt-container repos are per-env shared and left alone.

**`doh_build_prebuilt_image` is the generic operator entry point.** `uv run manage.py doh_build_prebuilt_image --account <acct> [--org <org>] [--env <env>] --source-dir <path> --ecr-repo <name> --tag <version> [--overwrite]`. Creates the per-env ECR repo (scan-on-push + keep-last-20 lifecycle policy shared with `SidecarEcrStack` via a new `ecr_utils.apply_lifecycle_policy` helper), refuses to overwrite an existing tag unless `--overwrite`, delegates the actual build to `ecr_utils.build_and_push_docker_image`. Explicitly **not** hard-coded to sidecar-mcp — any future prebuilt container runs through this same command. The `_aws_account_resolver` shared helper was extracted from the duplicated `_get_aws_account`/`Environment`/`iam_utils.get_assumed_role_session` pattern scattered across four existing commands; only the new command is wired to it today (old commands migrate opportunistically).

**Naming gotcha caught during implementation.** `argparse` collides with Django's built-in `--version`. Renamed the command's tag argument to `--tag`. The management command takes `--tag 0.1.0`; the AppTemplate stores `version: "0.1.0"` in its container dict. Not ideal symmetry, but the Django-global constraint is what it is.

**Hermes config plumbing.** `config.yaml.template` ends with a static `mcp_servers.sidecar: { url: "http://127.0.0.1:7777/mcp", enabled: true, timeout: 120 }` block. No auth header — loopback is the boundary. Hermes's entrypoint (`entrypoint.sh:49`) only writes `config.yaml` from the template on first boot, so for the greenfield workspace new deploys pick this up automatically; any future change to the block requires the documented EFS `config.yaml` regeneration step.

**The sidecar-mcp container's upstream credential fields** (GITHUB_TOKEN, JIRA_API_TOKEN, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN, CONFLUENCE_API_TOKEN, DATADOG_API_KEY, DATADOG_APP_KEY, GROUNDCOVER_API_KEY, CONTROLMONKEY_API_TOKEN, CLOUDFLARE_API_TOKEN, GITLAB_TOKEN) are seeded with `value=""`, which triggers the fall-through to `devopshero/{env_slug}/shared-secrets`. Ops populates that bundle once per env; every Hermes in the env inherits everything automatically. Known consequence accepted up front: the aggregator's capabilities are bounded by the lowest-privilege tenant in the env. Acceptable for a pre-beta shared Slack bot with read-leaning service-account scopes. Per-user credentials would require the rejected sidecar-per-task shape; that door stays closed for now.

**MCP tools auto-attach to the Slack gateway.** Verified against Hermes `v2026.4.16` source: `hermes_cli/tools_config.py:495-593` (`_get_platform_tools`) auto-includes every enabled MCP server into the Slack tool universe *unless* `platform_toolsets.slack` explicitly lists MCP names (allowlist) or uses the `no_mcp` sentinel. Our seed leaves `platform_toolsets.slack: [hermes-slack]` untouched — MCP tools attach for free. Slack also has a native approval UI (`/approve`, `/deny` in thread at `gateway/run.py:6770-6817`), so `approvals.mode: manual` remains usable; don't need to flip to `auto`.

**What didn't change.** The `App` Django model still carries identity/build fields (`container_port`, `health_check_path`, etc.) — those stay single-container for now, populated from `template.containers[alb_target]` at deploy-from-template time. The `save_blueprint` agent tool writes its flat `environment_variables`/`app_secrets` inputs into `containers[0]` (single-container chat flow) to preserve its existing MCP contract; multi-container configs come from templates, not the chat path. Auth sidecar implementation is untouched. The whole refactor is intentionally scoped to the template-driven, ALB-facing-plus-sidecar-tasks path.

**Key points:**

- **Internal-only ALB in DOH was considered and rejected as out of scope.** It's the better long-term answer than "bearer token on a public endpoint," but a bigger change than this feature warranted. If another internal-service use case appears, revisit.
- **`image_source` name is precise enough.** The discriminator names "where does the image come from for *this deploy*," not "where was it originally built." `dockerfile` = build now; `prebuilt` = reference. Future `git-clone-at-build-time` could become another `image_source` variant without breaking the shape.
- **Per-container logs are a visible win.** Log stream prefix is `{app_slug}-{container_name}`, so `doh_app_logs` operators see `my-hermes-hermes` vs `my-hermes-sidecar-mcp` streams separately. No tooling change needed; the log driver config is per-container already.
- **The "hardcoded for now, UI later" note on `alb_target_container`.** It's `null=True` on the model but every active template seeds it. The UI doesn't let users edit it yet — templates are code-authored. That's fine; when customer-authored templates land, the UI adds a picker over the container names.
- **CPU/memory bump on Hermes Slack: 1024/2048 → 2048/4096.** Two containers share the task budget. 4096 MiB is conservative; tune down after profiling a real deployment.
- **Tests.** 288 pre-existing tests continue to pass after fixture updates to the new shape (test_template_deploy_owner_field, test_abac_engine, test_env_sidecar_secrets). 4 new tests in test_multi_container_app_config cover the two-container Hermes Slack projection, dockerfile vs prebuilt image source, `alb_target()` lookup, and the secret collision rule (matching values union cleanly; mismatched literals raise `ContainerSecretCollision`).
- **Migration path for existing deploys: none needed.** Greenfield workspace, no existing AppTemplates or deployments. If this change lands on an existing DOH instance later, the `0044` migration would drop the legacy columns and blueprints would lose their flat env/secrets — that's destructive and would need a data migration. Documented in the plan as out-of-scope.
- **Operator step before first Hermes Slack deploy:** build and push sidecar-mcp with `uv run manage.py doh_build_prebuilt_image --account <acct> --source-dir ../sidecar-mcp --ecr-repo sidecar-mcp --tag 0.1.0`. `SIDECAR_MCP_IMAGE_VERSION` in `seed_app_templates.py` must match the `--tag` used. Then `seed_app_templates` + deploy.

## 2026-04-21 00:19 - [Deployment] Force-purge app secrets on Remove App

**Conversation:** [2026-04-21-0019-32c51e6d.md](conversations/2026-04-21-0019-32c51e6d.md)

When the user ticks "Also delete AWS Secrets Manager secrets" in the Remove App modal, the removal now deletes secrets with `ForceDeleteWithoutRecovery=true` instead of the previous default 7-day recovery window. The motivation is redeploy ergonomics: AWS reserves a deleted secret's name for the full recovery window (min 7 days), so re-creating an app with the same slug in the same environment would fail with `InvalidRequestException: ... already scheduled for deletion` until the window elapsed or the user manually restored + force-deleted.

**How it's wired.** The plumbing already existed — `secrets_utils.delete_secrets_matching_prefix(..., force_immediate=True)` at `secrets_utils.py:380-386` maps to `ForceDeleteWithoutRecovery=True`. The executor at `app_remove_executor.py:110` was passing `force_immediate=False`. One-line flip to `True`. No new flag on `AppRemovalJob`.

**Why inherit from `delete_secrets` instead of adding a separate `purge_secrets` flag.** Three options were on the table:

1. Always force-delete regardless of flag.
2. New `AppRemovalJob.purge_secrets` toggle in the modal alongside `delete_secrets` / `delete_efs_data` / `delete_policies`.
3. Always force-delete *when* `delete_secrets=True` — i.e. "delete secrets" implies "purge."

Picked #3. The user has already explicitly opted into secret deletion via a checkbox in a confirm-destruction modal, which is the moment the safety net should be removed. Adding a second checkbox (#2) would ask the user to reason about AWS recovery-window semantics, which most deployers won't have context for. The intent signal ("I want to redeploy cleanly") is already fully captured by `delete_secrets`.

**Tradeoff that's now the user's problem.** No undo. If a user accidentally ticks `delete_secrets` during Remove App, the secrets are gone immediately — no 7-day restore window. The confirm modal's existing "type the app name to confirm" gate is the sole safety net. Acceptable because (a) the checkbox is unticked by default, (b) the confirmation gate is already stronger than the recovery window for accidental clicks, and (c) the user's most common failure mode is the opposite — removing an app, trying to redeploy, and hitting the scheduled-deletion conflict.

**UI copy.** Dropped "Scheduled with a 7-day recovery window." from the checkbox label in `_app_remove_confirm_modal.html:58` — no longer true and would mislead users into thinking they have a restore path. Didn't replace it with a "permanent, no recovery" warning because the modal is already framed as destructive and the type-name confirm is the real gate.

**Key points:**

- `secrets_utils.py` already exposed `force_immediate`; no changes to the utility layer.
- `IncludePlannedDeletion=False` in the list call means this doesn't re-purge secrets already scheduled for deletion from a prior removal — those still need the `doh_secrets purge-deleted` management command.
- Only affects the "Remove App" path. Bulk/env-level secret deletion via `doh_secrets delete` still defaults to the 7-day window; `--force` remains opt-in there because the CLI operator is typically reasoning about a broader blast radius.
- If we ever surface "restore a removed app" as a product feature, this decision needs revisiting — right now there's nothing to restore the secrets *into* anyway.

## 2026-04-21 00:08 - [Deployment] Disable Hermes "Check for updates" banner by dropping .git

**Conversation:** [2026-04-21-0008-05a010f8.md](conversations/2026-04-21-0008-05a010f8.md)

The Hermes WebUI ships a "Check for updates" feature that runs `git fetch && git rev-list --count HEAD..origin/…` against two repos on every browser boot and shows an update banner if either is behind. The user wanted it off **permanently** — not just toggled off in settings, since any user can flip it back on via the Settings panel. Solved it by removing the upstream-tracking side of one repo entirely: `rm -rf /opt/hermes-defaults/hermes-agent/.git` in the Dockerfile immediately after the `git clone`.

**How the upstream feature is wired** (`ghcr.io/nesquena/hermes-webui:0.50.126`, extracted under `/apptoo/`):

- `api/updates.py:162` — `check_for_updates()` is the server entry point. Calls `_check_repo(REPO_ROOT, 'webui')` and `_check_repo(_AGENT_DIR, 'agent')`. Results cached 30 min.
- `api/updates.py:125` — `_check_repo` early-returns `None` if `(path / '.git').exists()` is false. This is the kill-switch we're exploiting.
- `api/routes.py:706-739` — `/api/updates/check` endpoint. First checks the `check_for_updates` setting (`routes.py:708`); if off, returns `{"disabled": True}`. If on, runs the update check.
- `api/config.py:1375` — `"check_for_updates": True` in `_SETTINGS_DEFAULTS`, persisted in `STATE_DIR/settings.json`. Toggle in Settings panel writes there.
- `static/boot.js:800-806` — on every boot the browser fires `/api/updates/check`; shows `_showUpdateBanner(d)` if `d.webui.behind > 0 || d.agent.behind > 0`.

**What the image actually ships.** I first inferred (incorrectly) that `.git` lived inside the WebUI image. User pushed back; re-extracting confirmed `/apptoo/.git` does **not** exist — the WebUI image baked its version into `api/_version.py` instead. So the webui half of `_check_repo` was already a silent no-op in production. The only live check was against the hermes-agent repo, which **our** Dockerfile brings in via `git clone --depth 1 --branch v2026.4.16 …/hermes-agent.git` and then `entrypoint.sh:70-72` `cp -r`s to EFS on first boot, `.git` and all.

**Why deleting `.git` is a cleaner fix than any alternative.**

- **Pre-seeding `settings.json` with `check_for_updates: false`** was the first option I proposed. Rejected by user because it's user-toggleable — the whole point of the kill is "no matter the user's clicking the switch."
- **Patching the Python source** (via a 4th entry in `template_repos/hermes_agent/patches/`) would work but the existing `apply.py` targets `$HERMES_DIR/hermes-agent`, not the WebUI `/apptoo/` tree that's baked into the image layer. Would have required extending `apply.py` or doing a Dockerfile-time sed, and we'd have to re-verify the patch after every WebUI version bump.
- **Deleting `.git`** makes `_check_repo` short-circuit at `updates.py:125` before any network call. The Settings toggle becomes inert: flip it on, the route runs, `_check_repo` returns `None` for both repos, `boot.js:805` sees no `behind > 0`, banner never fires. Immune to upstream refactors of the update logic as long as the `.git` guard stays in place.

**Where we landed.** One line added to `template_repos/hermes_agent/Dockerfile:9-11`:

```Dockerfile
RUN git clone --depth 1 --branch v2026.4.16 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git
```

No `entrypoint.sh` change. Briefly added an unconditional `rm -rf "$HERMES_DIR/hermes-agent/.git"` on boot to clean up already-deployed EFS volumes, but user reverted that: "If it's only to clean up pre-existing, we don't need it." Correct call — the Personal template is early enough that there are no long-lived EFS volumes worth migrating, and keeping entrypoint.sh minimal is worth more than belt-and-suspenders.

**Side effect worth knowing.** This also disables the `hermes update` self-update flow on the agent repo (documented in the template README). Since we already pin `v2026.4.16` in the Dockerfile, that's the intended behaviour — updates come from us bumping the image, not from in-app pulls.

**Key points:**

- The `.git` guard at `updates.py:125` is load-bearing for this approach; worth a re-verification if we ever bump the WebUI image past `0.50.126` (check that the guard still exists and the banner still requires `.git`).
- WebUI version detection (`api/_version.py` fallback at `updates.py:75-89`) is independent of the `.git` presence, so the "Hermes vX.Y.Z" string in the UI still works after this change.
- Didn't touch the Settings toggle UI — it stays visible, just has no effect. Hiding it would require a `static/panels.js` patch, not worth the maintenance.

## 2026-04-20 23:45 - [DomainModel] Extend Remove App with a policies-cleanup checkbox

**Conversation:** [2026-04-20-2345-0f9b0797.md](conversations/2026-04-20-2345-0f9b0797.md)

The "Remove App" action shipped yesterday (`9f83afd`) leaned on FK cascades to wipe `DeploymentBlueprint`, `Deployment`, `DeploymentLog`, `AppPermissions`, `AppPermissionRequest`, and `ResourceTag`. `Policy` was missed because it has no FK to `App` — policies reference an app only through JSON `resource_conditions` like `[{"key": "app-name", "value": "<slug>"}]`. After removal those rows linger: the auto-created `Default: {app.name} open access` policy from `abac.create_default_app_policy` (`abac.py:660`) plus any admin-authored policies scoped to the same `app-name=<slug>` tag. They're runtime-harmless (the `app-name` tag is gone so they never match) but they pile up in the Policies UI as dangling references, and a future app with the same slug would inadvertently inherit them.

Added a third optional checkbox to the confirm modal ("Also delete policies targeting `app-name=<slug>`"), symmetric with the existing secrets / EFS checkboxes. Wired it through `AppRemovalJob.delete_policies` (new bool field + migration `0043`) into the executor, which runs the sweep inside the existing `transaction.atomic()` block right before `app.delete()` so the policy wipe and app deletion commit together.

**Key points:**

- **Why a checkbox and not an automatic sweep.** Consistent with the other two cleanup options: removal is reversible-ish (you could recreate the app with the same slug and re-grant), but policies can be shared/edited by admins, so we surface the action rather than making it implicit. Default unchecked — user has to opt in.
- **Match criterion is intentionally broad.** Any `Policy` with `resource_type="app"`, scoped to the app's organization, where *any* entry in `resource_conditions` matches `{"key": "app-name", "value": app.slug}`. If a compound policy has `[{"key": "app-name", "value": "x"}, {"key": "owner", "value": "alice"}]`, the whole row gets deleted — it's still wholly about this app. We don't surgically edit `resource_conditions` arrays; that's harder to reason about and the user can always recreate a more general policy.
- **Includes `is_system=True` rows.** The auto-created default open-access policy is flagged `is_system` for display purposes only, not as a deletion guard. The whole point is to clean it up.
- **Python-side filtering, not a JSON SQL query.** `resource_conditions` is a JSON list of dicts; filtering `[{"key": "app-name", ...}]` via `resource_conditions__contains=[{...}]` works on Postgres but not SQLite, and the volume (policies per org) is trivially small. Fetching candidates with `resource_type=APP` and walking them in Python is dialect-agnostic and plenty fast.
- **Runs inside the existing atomic block.** Placed before `app.delete()` inside the same `transaction.atomic()` — if the cascade delete fails (unlikely, but), the policy deletion rolls back too. Kept outside the earlier try/except that's scoped to `ClientError` from AWS; DB failures should propagate to the worker's top-level handler which calls `fail_from_worker` and reverts `App.status` out of `PENDING_REMOVAL`.
- **Cascade-comment updated.** Added a note to the comment in `app_remove_executor.py` that `Policy` has no FK to `App` and is handled explicitly above — the whole reason we missed this the first time was that the cascade comment made it feel exhaustive.

## 2026-04-20 23:44 - [Deployment] Disconnect HERMES_WEBUI_PASSWORD for sidecar-gated Personal template

**Conversation:** [2026-04-20-2344-e6df3d54.md](conversations/2026-04-20-2344-e6df3d54.md)

The Hermes Personal template has been shipping two redundant auth layers: the sidecar proxy (Okta SSO + ABAC) and Hermes WebUI's own `HERMES_WEBUI_PASSWORD` check. The open item in `sidecar_proxy_design.md` and `personal_assistant_deployment_state.md` had flagged this as a v1 follow-up. Disconnected both the password and — critically — the WebUI's bind address, because disabling auth without loopback-binding would have exposed the WebUI over the task ENI to the whole VPC.

**How `HERMES_WEBUI_PASSWORD` actually works upstream** (`api/auth.py` in `nesquena/hermes-webui` v0.50.126):

- `get_password_hash()` reads `os.getenv('HERMES_WEBUI_PASSWORD', '').strip()`. Empty/unset falls back to a persisted `password_hash` in `state/settings.json`; if that's also `null`, `is_auth_enabled()` returns `False` and `check_auth()` short-circuits to allow everything. No separate "disable auth" flag exists — absence of a password IS the disabled state.
- No trusted-proxy / header-auth support anywhere in the codebase. Grep for `X-Auth-User`, `X-Forwarded-User`, `Remote-User` returns zero hits. So the sidecar's injected `X-Auth-*` headers are currently unused by the WebUI; upstream integration is still deferred.

**The isolation trap I caught before shipping the password removal.** The app container in a Fargate `awsvpc` task shares the ENI with the sidecar. Published image sets `ENV HERMES_WEBUI_HOST=0.0.0.0` (`api/config.py:31` reads this env var; Python default is `127.0.0.1`, Dockerfile overrides to `0.0.0.0`). Task SG is `devopshero-{env-slug}-default-sg` with ingress `all_traffic from vpc_cidr` (`deploy_base.py:193-195`). With the password gone but the WebUI still binding `0.0.0.0:8787`, anything inside the VPC could have hit it unauthenticated. Fixed by setting `HERMES_WEBUI_HOST=127.0.0.1` as a config-category runtime variable on the Personal template.

**Why ECS `portMappings` don't save you here.** In Fargate awsvpc, port mappings are informational (they feed the ALB target group registration and Service Connect). They don't firewall ports or prevent a container from listening on anything else. The real isolation boundary inside a task is *what address the process binds to*. You cannot per-container firewall inside a single task; they share one network namespace. The only way to get AWS-layer isolation between two containers is two separate ECS services with their own ENIs and SGs — roughly doubles the bill per app and breaks the shared-EFS / localhost-sidecar pattern. Standard service-mesh pattern (Envoy, Istio, App Mesh) is to bind the app loopback-only and let the sidecar own the public port; that's what we're doing.

**Splitting the password out by template, not removing it wholesale.** `_HERMES_CREDENTIAL_VARS` was used by both `HERMES_PERSONAL_TEMPLATE` and `HERMES_SLACK_TEMPLATE`. Slack template is ALB-exposed with no sidecar, so removing the password there would have opened its WebUI to the public internet. Extracted `HERMES_WEBUI_PASSWORD` into its own `_HERMES_WEBUI_PASSWORD_VAR` list, included it only in the Slack template. Personal drops it entirely.

**Why "remove entirely" vs "set value to empty string".** Setting `"value": ""` in the seed still flows through `_materialize_app_secrets` and `secrets_utils._resolve_secret_value` (`secrets_utils.py:31-37`). That function treats `""` as a *placeholder* that should fall back to the env's shared-secret entry. If `devopshero/{env-slug}/shared-secrets` happens to contain a `HERMES_WEBUI_PASSWORD` from any prior deploy, an empty seed value would get silently rehydrated and auth would turn back on. Removing the seed entry entirely means no ECS injection, no Secrets Manager key, no resurrection path.

**Key points:**

- **Dockerfile `EXPOSE`, ECS `portMappings`, and process bind address are three different things.** Only the last one firewalls network access in Fargate awsvpc. `EXPOSE` is documentation. `portMappings` register with ALB/Service Connect. The kernel's `bind()` decides what's actually reachable.
- **`HERMES_WEBUI_HOST=127.0.0.1` is load-bearing.** Without it the VPC-reachable bind plus disabled auth would have been a worse posture than before. Both go together.
- **Upstream WebUI has no X-Auth-\* header support.** Sidecar still injects `X-Auth-User/Sub/Email` (`sidecar_proxy_design.md:98`) but nothing reads them on the WebUI side. Closing that gap requires an upstream patch or a WebUI feature — still deferred.
- **Migration is automatic for new deploys, manual-ish for existing ones.** Removing the key from the seed stops ECS from injecting it on the next redeploy; the stale password in the per-app `devopshero/{env-slug}/{app-name}/secrets` entry just becomes an unused field. If anyone ever clicked through to the WebUI's Settings panel and saved a `password_hash` to `webui-mvp/settings.json`, it would persist on EFS — unlikely given the sidecar was in front, but a known edge case.
- **Worker doesn't need restarting.** Deploy path reads `AppTemplate.runtime_variables` from the DB, not from the Python module at process start. `uv run manage.py seed_app_templates` is the only thing needed to land the new shape.
- **Existing test references survive.** `test_env_sidecar_secrets.py` uses `HERMES_WEBUI_PASSWORD` as a stand-in key name in fake secret fixtures — not coupled to the seed. All 20 tests in the relevant suites still pass.

## 2026-04-20 22:59 - [Deployment] Pin Hermes WebUI and hermes-agent versions in the template Dockerfile

**Conversation:** [2026-04-20-2259-d3e8c08d.md](conversations/2026-04-20-2259-d3e8c08d.md)

The Hermes template baked together two independently-versioned upstreams: `ghcr.io/nesquena/hermes-webui` (pinned at `0.50.87` via the `FROM` line) and `NousResearch/hermes-agent` (cloned with `git clone --depth 1` — no branch, no tag, no SHA). That meant image builds weren't reproducible: two builds days apart would ship different agent code, and the patches under `template_repos/hermes_agent/patches/` target specific upstream line numbers in `run_agent` and `auxiliary_client`, so silent drift could have broken the patch apply at boot. The "free upstream fixes on rebuild" property was cute but not worth the reproducibility hit.

Bumped both to the latest releases and pinned the agent to a tag. New state:

- `FROM ghcr.io/nesquena/hermes-webui:0.50.126` (was `0.50.87` — 39 patch versions of drift; still 0.50.x series so nominally backward-compatible).
- `git clone --depth 1 --branch v2026.4.16 …` for hermes-agent (v0.10.0 in Nous's own versioning scheme, which maps to the CalVer git tag `v2026.4.16`).

**Key points:**

- hermes-agent uses CalVer tags (`v2026.4.16`, `v2026.4.13`, …). Nous also prints a semver-style label in release names ("Hermes Agent v0.10.0") but the actual git tag is the CalVer form — that's what goes in `--branch`.
- hermes-webui release tags are `v0.50.126` but the container image tag drops the `v` (`0.50.126`). Easy foot-gun.
- Verified the three DOH patches still apply cleanly against the pinned `v2026.4.16` tree by running `apply.py` inside a throwaway `python:3.12-slim` container with `git` + `patch`. All three succeed, plus the `bedrock_aux_client.py` overlay copies in. No drift from 0.9.0 → 0.10.0.
- The pin does NOT roll out to existing customer deployments automatically. EFS already contains a `hermes-agent/` dir, so the entrypoint's first-boot seed (`if [ ! -d "$HERMES_DIR/hermes-agent" ]`) is a no-op. Updating existing installs still requires either `hermes update` (user-initiated) or an EFS wipe. Only the WebUI binary, which lives in the ephemeral container filesystem, updates on redeploy.
- Cost of the pin: takes on a manual bump cadence for hermes-agent (mirroring the cadence we already have for hermes-webui). Worth it because the patch system is explicitly designed to absorb upstream changes idempotently, so the "soft-landing" property doesn't require an unpinned clone to work — it just requires periodic rebuilds against newer pins.

## 2026-04-20 16:36 - [Bugfix] Sidecar broke Hermes WebUI — streaming and HTTP/1.0 framing

**Conversation:** [2026-04-20-1638-d5543321.md](conversations/2026-04-20-1638-d5543321.md)

First real end-to-end use of the sidecar in front of Hermes exposed two independent proxy bugs. The WebUI streaming reply arrived as a blob instead of tokens trickling in, and static assets (`style.css`, `i18n.js`) failed with intermittent `ERR_HTTP2_PROTOCOL_ERROR`. Both were in `sidecar/sidecar/proxy.py` — the PDP path, JWT verification, and per-env trust anchors were fine.

### Bug 1 — buffered response killed streaming

The original `proxy_to_upstream` did `upstream_response = await http_client.request(...)` followed by `Response(content=upstream_response.content, ...)`. `httpx.AsyncClient.request()` reads the entire body before returning, so SSE from Hermes was fully accumulated and shipped as one chunk. For a 500-token reply at ~10 tok/s that meant ~50s with zero bytes, then all bytes at once — the UI looked dead until generation finished.

**Fix:** switched to `http_client.build_request(...)` + `http_client.send(..., stream=True)`, then returned Starlette `StreamingResponse` wrapping `upstream_response.aiter_raw()`, with `BackgroundTask(upstream_response.aclose)` to release the upstream connection after the body drains.

Before implementing, we ranked the whole perf surface (uvloop/httptools, split httpx pools, PDP cold-call latency, JWKS sync fetch on cold start). For single-user Hermes — one WebUI, one sidecar, one task — only the response-streaming fix was mandatory. Everything else was optimization that doesn't matter at concurrency=1. Decided explicitly to skip them. Worth revisiting if we put multiple users behind one sidecar (not the current shape).

### Bug 2 — outbound chunked encoding against HTTP/1.0 upstream

After shipping Fix 1, static CSS/JS started failing. First stab: I had stripped `Content-Length` from response headers, forcing Starlette to re-frame everything as chunked. Browser asked ALB, a second question: why do small fixed-length assets fail? Partial fix: stopped stripping `Content-Length`. Didn't solve it.

Pulled the sidecar's CloudWatch logs (had to add a `--sidecar` flag to `doh_app_logs` — the existing command hardcoded the app-container log stream as `{app_slug}/{app_slug}/{task_id}`; sidecar lives at `{app_slug}-sidecar/{app_slug}-sidecar/{task_id}`). Logs showed the smoking gun:

```
httpx HTTP Request: GET http://127.0.0.1:8787/static/icons.js "HTTP/1.0 200 OK"
```

plus `httpcore.ReadError` mid-`aiter_raw`. Hermes's embedded HTTP server speaks HTTP/1.0, which does not support `Transfer-Encoding: chunked`. When I switched to streaming, I always passed `content=request.stream()` to `build_request` — even on bodyless GETs for CSS/JS. httpx reacts to any async-iterator content by setting `Transfer-Encoding: chunked` on the outbound request. Hermes got request framing it couldn't parse and closed the connection in the middle of the response.

**Fix:** only pass `content=request.stream()` when the inbound request actually has a body, detected via `content-length` / `transfer-encoding` on the inbound headers. GET/HEAD go out clean.

### HTTP/2 ALB→target tangent

Considered whether making the ALB → sidecar leg HTTP/2 would have helped. Answer: no, for four reasons: (1) wouldn't have fixed either bug, since the broken leg was sidecar → Hermes; (2) uvicorn+h11 is HTTP/1.1-only, HTTP/2 means switching to Hypercorn; (3) no real wins at concurrency=1; (4) the upstream hop stays HTTP/1.x regardless — sidecar is always the translation boundary. The simplest stack that works is the right one for "transparent and fast."

**Key points:**
- **Streaming is mandatory; everything else on the perf list is not** — at single-user, only `aiter_raw` + `StreamingResponse` materially changes behavior.
- **httpx adds `Transfer-Encoding: chunked` whenever request `content` is an iterator** — even for GETs. Only pass `content=` when the inbound request has a body (`content-length` or `transfer-encoding` header present). Applies to anything proxying to HTTP/1.0 targets.
- **Don't strip `Content-Length` from response headers** — forcing chunked re-framing on fixed-length bodies triggers `ERR_HTTP2_PROTOCOL_ERROR` when ALB translates to HTTP/2. Pass the upstream framing through verbatim; SSE responses have no `Content-Length` so Starlette emits chunked naturally.
- **Hop-by-hop stripping stays** — RFC 7230 hop-by-hop set (`transfer-encoding`, `connection`, etc.) must still be stripped both directions; `Content-Length` is not hop-by-hop.
- **`doh_app_logs --sidecar`** — the sidecar container's log stream segment is `{app_slug}-sidecar`, matching `container_name=f"{app_config.app_name}-sidecar"` in `deploy_app.py`. New flag avoids duplicating the command.
- **Test-transport quirk** — `httpx.MockTransport` pre-materializes `httpx.Response(200, text=...)` bodies, which trips `StreamConsumed` when the proxy calls `aiter_raw()` after `stream=True`. Tests now use a `_streamed_body(*chunks)` helper returning an async generator.



**Conversation:** [2026-04-20-1934-978400ed.md](conversations/2026-04-20-1934-978400ed.md)

Each sidecar-enabled env was provisioning three Secrets Manager entries: `shared-secrets` (ECS task role reader), `sidecar-jwt-key` (auth Lambda), and `oidc-config` (auth Lambda). The two Lambda-side secrets always live and die together — the auth Lambda is the only principal that reads either of them — so keeping them split was pure boilerplate: two `create_secret` calls, two ARNs plumbed through `AuthLambdaInputs`, two IAM resources, two env vars, two cached globals in the Lambda.

Collapsed them into `devopshero/{env}/sidecar-auth-config` with a nested shape: `{"oidc_config": {...}, "jwt_key": {...}}`. The ECS-side `shared-secrets` entry is deliberately left alone — it has a different reader (task role), is mutated at runtime by operators via `doh_secrets shared-set`, and holds user-supplied app env vars (`OPENAI_API_KEY`, etc.); folding it in would widen the Lambda's access over customer data for no real gain.

**Key points:**
- **Only Lambda-side secrets folded.** Kept the blast-radius line intact: sidecar task role still can't see the JWT private key or the OIDC client_secret, and the auth Lambda still can't see customer env vars.
- **Rotation invariance preserved.** `ensure_env_sidecar_auth_config_exists` reads the existing secret first, carries `jwt_key` forward unchanged if present, and only refreshes `oidc_config` from `Organization.oidc_*` on re-run. That upholds the existing "never rotate the keypair after creation" invariant (rotation would require a coordinated Lambda + all-sidecars redeploy, documented in `docs/sidecar_proxy_design.md`).
- **No migration shim.** User is tearing down & recreating environments, so old secret names just stop being written — dangling AWS entries get cleaned up by teardown.
- **Single cached config object in the Lambda.** Replaced `_cached_oidc` / `_cached_jwt_key` + two loaders with one `SidecarAuthConfig(oidc, jwt_key)` dataclass and one `load_sidecar_auth_config()` call. Call sites now do `.oidc` / `.jwt_key` on the returned object.
- **Why not fold `shared-secrets` too?** Briefly considered — rejected because: different principals (ECS task role vs Lambda role), different mutability profiles (shared-secrets is user-mutable at runtime, jwt_key is immutable, oidc_config refreshes per-deploy), and `shared-secrets` uses ECS JSON-key-scoped injection (`DOH_SIDECAR_TOKEN::` syntax) for flat scalar env vars, which doesn't compose with nested objects.

## 2026-04-20 13:13 - [Bugfix] "Deployed to Environments" showed stale Succeeded after teardown

**Conversation:** [2026-04-20-1319-330d3a27.md](conversations/2026-04-20-1319-330d3a27.md)

The app detail page has two stacked sections: "Deployed to Environments" (one summary row per env) and "Recent Deployments" (chronological list). After tearing an app down, the summary row still showed a green "Succeeded" pill with a 15-hour-old timestamp, while the list below correctly showed "Torn Down" at the top. Bug report from looking at the Hermes deploy in Humanity Rules Sandbox.

### Root cause

`_build_deployed_environment_rows()` in `devopshero_app/views/apps.py` picks the deployment to display per blueprint using a priority `Case` expression, ordered by `(status_priority, -created_at)`:

```python
When(status__in=Deployment.IN_PROGRESS_STATUSES, then=Value(0)),
When(status=Deployment.Status.SUCCEEDED, then=Value(1)),
default=Value(2),  # everything else — FAILED, TORN_DOWN, TEARING_DOWN, TEARDOWN_PENDING
```

The intent of the tiering was sound: keep a failed redeploy from hiding the last success. But teardown states got lumped into the same "default" tier as `FAILED`, so an older `SUCCEEDED` (priority 1) always beat a newer `TORN_DOWN` (priority 2). That's why the pill reflected the pre-teardown state.

### Fix

Two tiers, re-scoped:

- Tier 0 — **transient ops in flight.** Switched from `IN_PROGRESS_STATUSES` to the existing `TRANSIENT_STATUSES` tuple (models.py:1017-1021), which already bundles deploy-in-progress with `TEARDOWN_PENDING` / `TEARING_DOWN`. Active teardowns now surface the same way an active redeploy does.
- Tier 1 — **terminal authoritative conclusions.** `SUCCEEDED` and `TORN_DOWN` together, picked by recency. So a redeploy after a teardown shows Succeeded again, and a teardown after a successful deploy shows Torn Down.
- Default tier 2 — `FAILED` / `ROLLED_BACK`. The "failed redeploy doesn't mask last success" behavior is preserved.

### What I got wrong first

Initial plan put `TEARDOWN_PENDING` and `TEARING_DOWN` in tier 1 with `SUCCEEDED`/`TORN_DOWN`. User caught it: those are in-flight, not terminal — they belong with `IN_PROGRESS_STATUSES`. The repo already had `Deployment.TRANSIENT_STATUSES` doing exactly that grouping; I should have grepped for it before drafting the plan instead of inventing a new `AUTHORITATIVE_STATUSES` constant. Reuse over invent.

### Verification

Change is a pure query-ordering tweak; behavior table worked through by hand:

| Newest → oldest                                   | Shown         |
| ------------------------------------------------- | ------------- |
| TORN_DOWN ← SUCCEEDED                             | Torn Down ✅  |
| FAILED ← SUCCEEDED                                | Succeeded ✅  |
| TEARING_DOWN ← SUCCEEDED                          | Tearing Down ✅ |
| DEPLOYING ← TORN_DOWN                             | Deploying ✅  |
| SUCCEEDED ← TORN_DOWN (redeploy after teardown)   | Succeeded ✅  |

No template changes needed — `_app_blueprint_row.html` already handles `torn_down` in its action-button logic (line 95).

## 2026-04-20 12:57 - [Deployment] Per-user App Name prefill on the deploy-from-template form

**Conversation:** [2026-04-20-1259-291de471.md](conversations/2026-04-20-1259-291de471.md)

Added a `prefill_name` pattern field to `AppTemplate` so owner-bound templates (today: Hermes Personal) pre-populate the App Name on the deploy form as `hermes-{username}{index}`. The goal: get one-click deploys for every user in an org without asking them to hand-craft a unique slug. Value is computed server-side at GET time, picking the lowest free `{index:02d}` in the org; admins get a live-updating input when they change the Owner dropdown.

### Design choices

- **Prefill lives on the template, not the view.** Different owner-bound templates will want different prefixes ("hermes-...", "notes-...", etc.), so the pattern string belongs next to the rest of the template config (`prefill_name` CharField on `AppTemplate`, empty string = fall back to `template.name`). Keeps the deploy form generic.
- **Two tokens, no template engine.** `{username}` and `{index}` via plain `str.format`. No Jinja/Django-template interpreter for three-character substitutions. If we ever need more tokens (owner's team, env, etc.) we can bolt them on without pulling in a rendering library.
- **Username sanitization: email local-part + alphanumerics only, lowercased.** `vmendi@gmail.com` → `vmendi`; `john.doe@company.com` → `johndoe`; `Alice_99` → `alice99`. The initial plan only split on `@`, but the user pushed back: dots and other punctuation survive into the slug otherwise (`hermes-john.doe00` works but looks ugly, and `slugify` would mangle non-ASCII). Stripping to `[a-z0-9]` keeps the output tight and collision-friendly.
- **Index collision resolution by query, not sequence count.** `_compute_default_app_name` loops `index` 0..99 and does one `App.objects.filter(organization=org, slug=slugify(candidate)).exists()` per step, returning the first free one. Using `COUNT(*)` would be cheaper but wrong when earlier users have been deleted and their slugs freed. Loop cap of 100 is deliberate — if someone has 100 hermes PAs they can type their own name.
- **Live owner→app_name update for admins only.** Non-admins see a read-only owner (locked to self), so the value is right from the start. For admins, the view precomputes a `{owner_id: computed_name}` dict and ships it as `json_script`; a tiny inline script wires the `<el-select>` `change` event to replace `#app_name`'s value. No HTMX round-trip — the prefill is a suggestion, so ship it all on the initial render. Index is frozen at GET time; if two admins race, one eats a dup-slug error on submit, which is acceptable.
- **Error rerender path preserves user input.** When validation fails, `_handle_deploy` passes through whatever the user typed rather than recomputing the prefill, so they don't lose their edits.

### Things that bit

- **Django template partials have isolated scope.** The owner dropdown uses the shared `_dropdown_select.html#dropdown_select` partial via `{% include ... %}`. The map + script had to live in the parent `template_deploy_form.html` next to the include, not inside the partial — the partial doesn't know about `owner_prefill_map`. Consistent with the `views/CLAUDE.md` note about `partialdef` isolation.
- **`<el-select>` from Tailwind Plus Elements fires a standard DOM `change` event**, so plain `addEventListener("change", ...)` works — no custom event name needed. The hidden `<select>` it wraps exposes `value` normally on the target.
- **Dev data already had `hermes-vmendi00`**, so the first smoke test returned `hermes-vmendi01`. Good signal that the "find next free index" path actually runs; would have been easy to miss if I'd only tested against a fresh org.

### Verification path

Ran migrations, re-seeded, then hit `/deploy/from-template/hermes-personal/` and `/deploy/from-template/hermes-slack/` via curl with a real session cookie. Confirmed Personal renders `value="hermes-vmendi01"` in the App Name input and the `owner-prefill-map` `<script>` JSON carries every org member → their computed prefill. Confirmed Slack falls back to `value="AI Assistant — Hermes (Slack)"` (empty `prefill_name`, no map, no script). Helper unit-checked via `manage.py shell`: `vmendi@gmail.com → vmendi`, `john.doe@company.com → johndoe`, `Alice_99 → alice99`.

## 2026-04-19 21:59 - [Deployment] End-to-end test of the sidecar proxy against real AWS, plus four fixes it surfaced and a PDP decision cache

**Conversation:** [2026-04-19-2201-e2cf6c68.md](conversations/2026-04-19-2201-e2cf6c68.md)

Designed and ran a comprehensive end-to-end test for the sidecar proxy feature built in the previous session. The test deploys a real Hermes Personal Assistant into Humanity Rules Sandbox (new env `sidecar-e2e` under the shared `chsandbox.com` hosted zone), stands up a mock PDP alongside the auth Lambda, mints `doh_session` cookies directly from the env's JWT private key (bypassing Okta), and curls the deployed app with owner / non-owner / tampered / no-cookie cases. First-real-AWS run surfaced four bugs and one real-browser visit later surfaced a fifth. Also added a PDP decision cache in the sidecar after the browser test showed ~30 PDP calls per page load.

Three new management commands and a set of CDK plumbing landed: `sidecar_mint_cookie` (standalone JWT minter — reads the env's private RSA key from Secrets Manager, emits a valid `doh_session` for a given user), `sidecar_e2e_test` (the 9-phase orchestrator), and a pure-stdlib Lambda at `lambdas/pdp_mock/` with an `PdpMockLambdaStack` that mirrors the auth-Lambda pattern.

### Design choices

- **Mock the PDP rather than tunnel to localhost.** The sidecar inside Humanity Rules Sandbox's VPC can't reach the developer's laptop, and deploying the control plane to prod just to test a feature is a big lift. An allowlist-only mock PDP (stdlib handler, ALB-as-target-for-Lambda, host-based rule on `pdp-mock.<env-domain>` at reserved priority 11) keeps the test hermetic to AWS and still exercises every wire in the sidecar → PDP path. The real ABAC engine is unit-tested separately; we aren't missing a class of failure mode by mocking here.
- **Bypass Okta by minting JWTs directly from the env's signing key.** The auth Lambda's only job is to mint exactly that JWT after a successful OAuth round-trip. If Okta registers the correct redirect URI, the Lambda just assembles the claims and signs — unit-tested. The test loads `devopshero/{env}/sidecar-jwt-key` from Secrets Manager and signs with the private half. Means the test doesn't depend on an Okta tenant being reachable, Okta policy rules being right, or the browser's cookie handling.
- **Run the deploy inline, not via the job worker.** `app_deployment_executor.run_deployment(deployment_id)` called directly from the orchestrator blocks until the deploy completes and surfaces errors crisply. The worker is better for real usage where the web UI enqueues and the worker drives asynchronously, but for a test we want tight feedback.
- **9 phases with `--skip-until-phase`.** Re-running after a fix never re-provisions the env or re-builds the sidecar image. Phases are: preflight / seed-abac / create-env / pdp-mock / deploy / smoke / mint / verify / cleanup.
- **Verify phase asserts *not-a-redirect-to-auth*, not an exact status code.** Hermes WebUI 302s to its own `/login` for unauthenticated WebUI sessions — still "sidecar allowed the request and proxied to upstream." The assertion checks that the sidecar's Location header is NOT `https://auth.<env>/start`. Deny (403) and auth-redirect cases stay exact-match.

### Bugs the test surfaced (real AWS runs only — none showed up in unit tests)

1. **ECS deployment circuit breaker tripping.** Sidecar-enabled task had Hermes exiting with `FATAL: DOH_LLM_PROVIDER=bedrock requires AWS_BEDROCK_ACCESS_KEY_ID, AWS_BEDROCK_SECRET_ACCESS_KEY, and AWS_BEDROCK_REGION`. Fresh env had no shared secrets for Bedrock creds. Fixed by propagating Bedrock creds (known-working values from the `default` env) into `devopshero/sidecar-e2e/shared-secrets` via `doh_secrets shared-set`. Also noted a lifecycle issue: `ensure_app_secrets_exist` preserved the first-created (empty) per-app secret, so adding shared secrets later didn't backfill — that led to a separate fix later in the conversation (see prior journal entry on `ensure_app_secrets_exist` healing behavior).

2. **ECS service load-balancer binding pointed at the app container, not the sidecar.** `service.attach_to_application_target_group(target_group)` picks the first essential container with a port mapping — the app container on 8787, not the sidecar on 8788. Result: ALB landed traffic on Hermes directly, health probes hit `/__sidecar/healthz` on the app and got 302 responses, circuit breaker fired. Fix: when `sidecar_enabled`, switch to explicit `target_group.add_target(service.load_balancer_target(container_name="...-sidecar", container_port=sidecar_listen_port))`.

3. **Lambda architecture / bundling-image mismatch — "Runtime.ImportModuleError: Unable to import module 'handler': /var/task/cryptography/hazmat/bindings/_rust.abi3.so".** My Mac is ARM64, so CDK's default bundling image produced ARM64 `.so` wheels for `cryptography`, but the Lambda function was deploying to x86_64 by default. Fix: pin both sides — `architecture=lambda_.Architecture.ARM_64` on the Function, and `public.ecr.aws/sam/build-python3.12:latest-arm64` as the bundling image via `DockerImage.from_registry(...)`. Confirmed via an end-to-end `curl` of the JWKS endpoint.

4. **`sidecar_mint_cookie` stdout polluted by `iam_utils`'s role-assumption banner.** `get_assumed_role_session` prints `🔑 Assuming role: ...` / `✅ Assumed role ...` to stdout. The mint command's stdout was supposed to be *only* the JWT so operators could pipe it to `curl -H "Cookie: doh_session=$(...)"`. The banner went to stdout first, so the resulting "cookie" had a 🔑 emoji and a newline embedded in it. Fix: `contextlib.redirect_stdout(sys.stderr)` around the role-assumption call only.

5. **Real-browser test of `https://hermes-vmendi00.chsandbox.com` — auth Lambda returning 400 "invalid rd parameter" for legitimate redirects.** ALB → Lambda integration delivers `queryStringParameters` values **percent-encoded**. The sidecar sends `rd=https%3A%2F%2F...`, the Lambda received that raw-encoded string, and `urlparse()` parsed `scheme=''` (because `:` is `%3A`) so `_validate_rd` rejected everything. The existing unit test passed `query={"rd": "https://..."}` — already-decoded — so synthetic events didn't catch it. Fix: `urllib.parse.unquote(v)` on every query-string value inside `_query_params`, plus a regression test that passes a `quote()`-d value and asserts the state JWT carries the decoded rd.

### Real-world run flow (what actually works now)

`sidecar_e2e_test` with `--aws-account "Humanity Rules Sandbox" --org humr --env-slug sidecar-e2e --hosted-zone chsandbox.com --owner vmendi@gmail.com --non-owner robert.thompson --yes` takes ~7 minutes cold, ~2 minutes warm (env already exists, sidecar image cached). All four verification flows return the expected statuses:
- Owner → HTTP 302 proxied to Hermes `/login` (sidecar allowed; Hermes does its own gate).
- Non-owner → HTTP 403 (sidecar got deny from PDP).
- Tampered JWT → HTTP 302 to `auth.<env>/start` (sidecar rejected invalid signature).
- No cookie → HTTP 302 to `auth.<env>/start`.

### PDP decision cache (separate sub-thread)

Browser testing `hermes-vmendi00` via the **default** env (real user, local Django as PDP via ngrok reserved domain `devopshero.ngrok.io`) showed ~30 PDP calls for a single Hermes page load — every static asset, every XHR. V1 had no caching, as called out in the plan.

Added a tiny in-memory cache in the sidecar: `sidecar/sidecar/pdp_cache.py`, keyed on `oidc_sub` alone (not `(sub, app_id)` — each sidecar serves exactly one app since `DOH_APP_ID` is baked in at deploy time, and v1 has no route-level overrides so path-keying would be dead weight too). 60-second TTL by default, `DOH_PDP_CACHE_TTL_SECONDS=0` disables. Deny decisions cache the same as allow. Concurrent cold lookups both hit PDP and both write — same answer, last-writer-wins, no correctness issue.

Initial implementation used a `CacheKey` dataclass with both `oidc_sub` and `app_id` — user pointed out `app_id` is redundant for the one-sidecar-per-app topology and I simplified to a bare `oidc_sub` string key. Kept the design decision inline as a comment explaining when to add path-keying back.

### DOH_PDP_URL for dev deploys

Realization mid-session: sidecars deployed from localhost had `_resolve_pdp_url()` defaulting to `http://host.docker.internal:8000` in `DEBUG=True`, which is unreachable from inside Humanity Rules Sandbox's VPC. Made the sidecar 503 on every request ("authorization service unavailable"). Hardcoded the dev fallback to `https://devopshero.ngrok.io` (user's reserved ngrok hostname — confirmed single-developer scenario, so no per-developer setting needed). Explicit `os.environ["DOH_PDP_URL"]` override still wins, so the e2e test's pdp-mock path is unaffected.

Also discovered a stale-module footgun: the running `run_job_worker` process imported `deploy_app.py` once at startup and kept the old module in memory. Editing the source and clicking "Redeploy" from the UI didn't pick up the new `DOH_PDP_URL` until the worker was restarted. Worth documenting — any deploy_app.py change requires a worker restart to take effect.

### Secondary: stale Repository row

Pre-existing `Repository(full_name="template/hermes-personal", clone_url="file:///tmp/x")` in my dev SQLite from an earlier `sidecar_simulate` fixture blocked Phase 4 on first run. `deploy_from_template` does `aget_or_create(full_name=...)` and `defaults=` only applies on create, so the stale row was reused. Manual fix pointed `clone_url` at the real `template_repos/hermes_agent/` path. Worth a note for future devs hitting the same pattern.

**Key points:**

- **Mock the PDP + mint cookies directly from the env key = hermetic e2e test.** No control-plane dependency, no Okta dependency; full sidecar pipeline exercised against real AWS resources.
- **Five bugs surfaced that unit tests didn't catch** — all of them were wire-format mismatches between synthetic test events and real AWS behavior (architecture mismatch, LB binding, percent-encoding, stdout pollution, ECS circuit breaker on missing env). This is why running against real AWS pays off even after 300+ unit tests pass.
- **Cache key is just `oidc_sub`**, because sidecar-per-app and no route overrides. Kept a comment explaining when path-keying becomes load-bearing. `ttl_seconds=0` is the disabled sentinel — preserved as the default in the `sidecar_config` test fixture so existing app-flow tests stay cache-free.
- **`sidecar_mint_cookie` is useful on its own** — paste the JWT into a browser's `doh_session` cookie and bypass Okta for manual UI debugging. Not a dev-mode-only command, but running it against a prod env should be gated by operator judgment.
- **Running the worker is stateful.** Any code change to `deploy_app.py` or anything `deploy_app` imports requires a job-worker restart for the next deploy to see the change. Non-obvious and easy to miss.
- **Hardcoding the ngrok URL is acceptable for a single-developer codebase.** A setting-based approach is correct for teams. Traded: one-line hardcode vs reading from `.env`. Both would be fine.

## 2026-04-19 21:01 - [Bugfix] `ensure_app_secrets_exist` heals empty shared-placeholder keys on redeploy

**Conversation:** [2026-04-19-2102-e2cf6c68.md](conversations/2026-04-19-2102-e2cf6c68.md)

`hermes-vmendi00` in Humanity Rules Sandbox failed first-deploy because `AWS_BEDROCK_*` were absent from `devopshero/default/shared-secrets`. `entrypoint.sh` aborts when `DOH_LLM_PROVIDER=bedrock` and those vars are unset, so ECS tripped the deployment circuit breaker. User populated the shared secret via `doh_secrets shared-set-from-env` and clicked Redeploy — it failed again for the exact same reason.

Root cause was in `secrets_utils.ensure_app_secrets_exist`: the merge path only added keys **missing** from the per-app secret. On first-deploy, keys with `""` placeholders were created (via `_resolve_secret_value("")` which, with no shared value present, leaves them `""`). On the next run, those keys were *present* (just empty), so the function early-returned with "all required keys exist" and never re-resolved them from the now-populated `shared_secrets`. ECS then injected `AWS_BEDROCK_ACCESS_KEY_ID=""` and the container aborted.

Fix: extend the merge path to also "heal" keys where `app_config.app_secrets[k] == ""`, `existing_values[k] == ""`, and `shared_secrets[k]` is truthy. Only those three conditions together — so non-placeholder literal `""` values (e.g. intentionally empty) aren't clobbered, and non-empty stored values are never overwritten.

**Key points:**

- The original `missing_keys` logic treated shared-placeholder behavior as a create-time concern only. Now shared-set + redeploy propagates values through the app secret without manual `put-secret-value`.
- Added a regression test (`test_heals_empty_existing_value_from_shared_secrets`) that seeds an app secret with empty placeholder + a non-empty unrelated key, then verifies the placeholder gets filled from shared secrets while the non-empty key is preserved.
- Not a bug in `doh_secrets shared-set` or `shared-set-from-env` — the shared secret side was fine. The propagation from shared → per-app is what was broken.

## 2026-04-19 20:04 - [DevEx] `doh_secrets shared-set-from-env`: push shared secrets from a local `.env`

**Conversation:** [2026-04-19-2004-cf6b48f6.md](conversations/2026-04-19-2004-cf6b48f6.md)

The first idea was a bash helper under `template_repos/hermes_agent` that would read named keys from a `.env` and shell out to `doh_secrets shared-set`. That would duplicate flag parsing, path handling, and quoting rules. Moving the behavior into Django keeps one entry point (`uv run manage.py doh_secrets`), reuses the same assumed-role session and `_run_shared_set` merge path as `shared-set`, and can use `python-dotenv` already in the project (same pattern as `infra_devopshero/sync_secrets.py`).

**`shared-set-from-env`** takes `--account`, `--env`, optional `--org` (identical targeting to `shared-set`), plus `--file` and one or more **key names**. Values are loaded with `dotenv_values`; any requested key missing from the file or present with a `None` value raises `CommandError` (empty string is allowed so `KEY=` matches CLI semantics). The command builds `KEY=VALUE` strings and delegates to `_run_shared_set` so `devopshero/{env}/shared-secrets` behavior stays identical.

**Key points:**

- Operators sync Hermes (or any) local secrets into shared env defaults without pasting values on the command line or maintaining a separate shell script in the template repo.
- No new dependencies; `Path` + `dotenv_values` only.

## 2026-04-19 19:00 - [Deployment] Enforce `devopshero/{env}/{app}/*` naming for all app-owned secrets

**Conversation:** [2026-04-19-1901-9f9d01b3.md](conversations/2026-04-19-1901-9f9d01b3.md)

App-owned secrets in customer accounts were inconsistently named: env-scoped helpers (`shared-secrets`, `sidecar-jwt-key`, `oidc-config`) already carried the env slug, but app secrets lived at `devopshero/{app}/secrets`, Aurora at `devopshero/{app}/aurora/{credentials,connection}`, and the ECS task role's `secretsmanager:GetSecretValue` resource scoped to `devopshero/{app}/*`. Without the env segment, the same app slug deployed into two environments on the same account would collide in Secrets Manager; env isolation was accidental rather than structural. Formalized the convention: **every secret an app owns lives under `devopshero/{env_slug}/{app_name}/*`**.

**What changed in the runtime paths:**

- `ensure_app_secrets_exist` gained an `env_slug` parameter and now writes `devopshero/{env_slug}/{app_name}/secrets`. The create-time `Description` includes the env too, so the AWS console is legible.
- `AuroraClusterStack` (which already received `env_slug`) uses it in both the credentials secret name passed to `rds.Credentials.from_generated_secret` and the derived connection secret name.
- `AppStack` task-role IAM statement resource is now `...:secret:devopshero/{env_slug}/{app_name}/*`, and `secretsmanager.Secret.from_secret_name_v2` imports the new name.
- `deploy()` threads `env_slug` into the single `ensure_app_secrets_exist` call site.

**Key points:**

- The change is safe because the user confirmed this is effectively green-field: no live customer apps were running under the old names (confirmed no live Aurora credentials either, which was the one concern — renaming `rds.Credentials.from_generated_secret` on an existing cluster would trigger CDK to **replace** the credentials secret and rotate the Aurora master password). With a clean slate we could rename everything uniformly in one pass.
- Out of scope: `infra_devopshero/` (DOH's own control plane) already uses `devopshero/prod/...` and stays as-is — that's the DOH server, not a customer app.
- The `delete-by-prefix` subcommand landed earlier in this branch is the natural cleanup tool if stragglers ever appear (e.g. `doh_secrets delete-by-prefix --subprefix devopshero/<app>/`).
- Test gap closed: `test_env_sidecar_secrets.py` had coverage for all three env-scoped sidecar helpers but nothing for `ensure_app_secrets_exist` itself. Added four cases (create, merge-preserving-existing, shared-secret placeholder resolution, noop-when-app_secrets-is-None) using the existing `FakeSecretsManager` stub.
- Docs/examples needed sweeping too, not just code: `docs/domain_model.md` Secrets Manager naming line, the repo-analysis agent's system prompt (two references) + its schema field description, `seed_prepare_demo.py` seeded IAM policy ARN, and `template_repos/hermes_agent/README.md`. Historical docs (`docs/journal.md`, `docs/conversations/*`, `docs/deprecated/*`, `.beads/issues.jsonl`) were intentionally left untouched — they're frozen-in-time records.

## 2026-04-19 18:53 - [DevEx] `doh_secrets delete-by-prefix`: bulk delete by Secrets Manager name prefix

**Conversation:** [2026-04-19-1853-23d4c42f.md](conversations/2026-04-19-1853-23d4c42f.md)

Operators sometimes need to remove many related secrets at once (e.g. after renaming an app slug, or cleaning up test data) without clicking through the console or scripting boto3 ad hoc. Added **`delete-by-prefix`** so the same cross-account assumed-role session as `list` / `purge-deleted` can target every **active** secret whose name **starts with** a given string, using AWS’s native name-prefix filter on `ListSecrets` (not a full client-side scan).

**Behavior:** `--subprefix` is required and must be non-empty after trim. Default deletion uses a **7-day recovery window**; **`--force`** matches `purge-deleted` semantics (`ForceDeleteWithoutRecovery`). **`--dry-run`** lists matches only. Implementation lives in `secrets_utils.delete_secrets_matching_prefix`; `IncludePlannedDeletion=False` so secrets already in the deletion queue are out of scope (those are handled by `purge-deleted`).

**Key points:**

- Prefix semantics are AWS’s: `subprefix` is the filter value for `Filters=[{"Key": "name", "Values": [subprefix]}]`, i.e. secret **name starts with** that string. That is a prefix in the string sense, so a value like `devopshero/default/foo` also matches `devopshero/default/foobar` (because `foobar` begins with `foo`). Operators should choose the longest unambiguous prefix for their intent (often including a trailing `/` when the naming scheme uses path segments).
- Safer default is recoverable delete; immediate wipe is explicit so a mistake on `--subprefix` still allows recovery during the window unless `--force` was used.

## 2026-04-19 18:23 - [DevEx] `doh_reset_org_abac`: full factory reset of org ABAC policies

**Conversation:** [2026-04-19-1823-836cd8f3.md](conversations/2026-04-19-1823-836cd8f3.md)

ABAC policy rows (`Policy`) for an org can drift after experiments in the Security UI, broken seeds, or demo scripts. Ad-hoc `shell -c` snippets are easy to get wrong (especially remembering to re-run `create_default_app_policy` for each app after a full wipe). Added **`doh_reset_org_abac`** so the same reset runs locally (`uv run manage.py`) and in production (`prod_manage.sh`).

**Behavior:** resolve `--org` by slug then name (same pattern as `doh_secrets` / `doh_app_shell`). Resolve bootstrap user via `--admin-email` (must be an org member) or the first `OrganizationMembership` with `Role.ADMIN`. Delete **all** policies for the org, call `abac.bootstrap_organization`, then `abac.create_default_app_policy` for every `App` (sidecar-enabled templates skip open-access inside that helper). Documented in the manage-commands skill.

**Key points:**

- Full factory means deleting every `Policy` for the org, not only `is_system=False`, so seed rows and per-app defaults are recreated cleanly; `bootstrap_organization` alone would not overwrite edited seed rows.
- Smoke run on local `humr` deleted 34 policies and restored 8 seed policies; the org had no apps so zero default app policies were created — expected.

## 2026-04-19 18:22 - [Deployment] Environment teardown discovers stacks dynamically by prefix

**Conversation:** [2026-04-19-1822-1bdc74aa.md](conversations/2026-04-19-1822-1bdc74aa.md)

`sidecar_e2e_test` (landed over the last few days) was the first code path to provision stacks that aren't part of the original "VPC + EFS + builder + cluster" quartet — specifically `devopshero-{env}-auth-lambda` and `devopshero-{env}-pdp-mock`, both of which wire themselves into the env's shared ALB (a reserved listener-rule priority) and create a Route53 alias record. The hardcoded list in `deploy_base.teardown()` at `devopshero_app/services/infra_customer/deploy_base.py:684` was never updated, so any teardown would have orphaned those two stacks plus their listener rules and DNS records. `auth-lambda` in particular is created by *any* sidecar-enabled app deploy (not just the test), so this was a live gap, not a test-only one.

### Options considered

- **A. Dynamic discovery by CloudFormation name prefix** — every env stack already uses `devopshero-{env_slug}-*`, so the naming convention is already a de facto contract (every `Fn.import_value` assumes it). Formalize it as the discovery mechanism. Iterate rounds because CloudFormation refuses to delete stacks whose exports are still imported, which gives us natural dependency ordering for free.
- **B. Database registry (`EnvironmentStack` model)** — explicit and debuggable, but introduces the dual-write problem (partial creates leave dangling rows or miss real stacks). Still needs a reconciliation pass against CFN. Most creation sites would have to be touched.
- **C. CloudFormation tag-based discovery** — `ListStacks` doesn't filter by tag server-side; would still be a client-side filter plus one more thing to remember to set on every new stack. Little gained over A.

Went with **A**. The naming convention was already load-bearing — there's no way to add a new env stack without adopting it because it's baked into every `Fn.import_value` used across stacks — so using it for discovery adds nothing new to remember.

### Implementation

- `cloudformation_utils.list_stacks_by_prefix(cf_client, prefix)` — paginated wrapper over `cf_client.list_stacks` that filters out `DELETE_COMPLETE`. AWS keeps deleted stack summaries for 90 days (verified against the boto3 `list_stacks` docs per the project's "verify AWS facts" rule), so without that filter we'd see ghost stacks from previous env lifecycles.
- `deploy_base.teardown()` rewritten to loop up to 5 rounds. Each round: `list_stacks_by_prefix` → attempt `delete_stack_and_wait` on every match → retry. CFN's export/import dependency rules do the ordering work for us: dependents delete first because their exports aren't imported by anyone; then the base stacks (VPC, shared ALB outputs) unblock. Bails with a clear error if a whole round makes no progress — that signals either a true cycle, a non-CFN orphan (stale Route53 record, ENI), or a `DELETE_FAILED` that needs human eyes.

### Non-obvious details

- **Why "rounds" instead of a topological sort.** Two reasons: (1) we'd have to fetch every stack's template exports/imports, which is an extra API call per stack and still wouldn't catch implicit dependencies (like ALB listener rules referencing targets in sibling stacks). (2) CFN already enforces the rule server-side — the round loop is just "let CFN tell us what's leaf right now." Simpler and correct by construction.
- **The `delete_stack_and_wait` 10-minute per-stack waiter is fine for this.** Worst realistic case is ~5 stacks × 10 min = 50 min, bounded. Teardown is already a long-running job worker task.
- **DELETE_COMPLETE filtering was necessary, not defensive.** Without it, a second teardown of a slug that had ever existed before would find phantom stacks and get stuck trying to re-delete them (the `delete_stack_and_wait` call would no-op on `stack_exists=False`, so it'd "succeed" but also never make progress, tripping the stall check incorrectly).

### What this unblocks

- `sidecar_e2e_test` teardown now works end-to-end in Humanity Rules Sandbox without manual cleanup.
- Any future env stack (per the naming contract) is automatically included — no teardown code change needed when we add more sidecar infra, shared observability stacks, etc.

## 2026-04-19 01:19 - [Deployment] Sidecar proxy for Personal Assistants: design + full implementation

**Conversation:** [2026-04-19-0121-c8212365.md](conversations/2026-04-19-0121-c8212365.md)

Designed and implemented the runtime access control for Personal Assistants (Hermes) end-to-end — the SSO + ABAC gate that makes per-employee URLs like `vmendi-hermes.chsandbox.com` only reachable by their owner. Started from a design conversation, landed a detailed plan, then shipped it across 12 commits. All unit tests green (284 Django + 15 sidecar + 12 Lambda = 311), and verified against the live dev server using a new `sidecar_simulate` management command that mints a realistic JWT and calls `/api/pdp/evaluate` locally with the full ABAC engine in the loop.

**Problem shape:** PAs are the first deployable that needs per-user authorization at runtime. The Hermes WebUI only has a shared password today; we need Okta-backed SSO + ABAC decisions in front of it, preferably in a way that generalizes to any future internal-tool deployment.

### Design decisions made during the conversation (these matter months from now)

- **Auth flow: single central auth endpoint per env, not per-app Okta redirects.** The cookie lives on the parent domain `.chsandbox.com`, so one login covers every sidecar'd app in the env. Okta registers exactly one redirect URI per env (`https://auth.<env-domain>/callback`); the original destination URL travels in the OAuth `state` parameter signed as a JWT, not in the redirect URI. Whitelisting per-user URLs doesn't scale and encourages open-redirect vulnerabilities.
- **Auth endpoint implementation: Lambda behind the existing ALB.** Started at "Fargate service" — cost and operational footprint arguments walked it down to Lambda, then Function URL, then **ALB-as-target-for-Lambda** which reuses the env's existing ALB, cert, and Route53 zone. No API Gateway. Payload cap is 1 MB — irrelevant for OAuth.
- **PDP: per-request call with in-memory cache, not embedded.** Considered embedding the ABAC engine in the sidecar with a shared library, rejected: centralizing the decision in DOH keeps semantics in one place, gives an audit trail for free, and eliminates the version-skew-between-two-codebases problem. Cache is fail-static on DOH outages.
- **Trust anchors, v1: two secrets per env, not two per app.** Key #1: JWT signing keypair (private held by auth Lambda, public served via JWKS). Key #2: `DOH_SIDECAR_TOKEN` bearer token the sidecar sends to the PDP — shared per env, since all sidecars in an env sit inside the same VPC trust boundary. User's explicit call: "Why would the sidecar lie? If it's compromised, we have bigger problems."
- **ABAC needed a new primitive.** Expressing "Alice can use alice-hermes" globally (one policy, N apps) needed a self-referential condition `username = $resource.owner`. Added as a small value-side reference in `abac.py:_conditions_match`. Rejected alternatives: (A) N per-user policies — noisy; (C) bolt "owner" on outside ABAC — undermines "ABAC is the single decision point."
- **Okta cardinality for v1: one app per org; restrict to single-env orgs.** Multiple envs would each need their own callback URI and thus their own Okta app. Deferred the `EnvironmentOIDCConfig` table; Humanity Rules Sandbox is single-env, so this doesn't block the pilot.
- **Sidecar language: Python, not Go.** User pushed back on Go — Python fits the rest of the repo. FastAPI + uvicorn + httpx + pyjwt. Slightly larger image (~80 MB) and slightly higher per-request overhead, but trivial at pilot scale and zero second toolchain.
- **Sidecar image distribution: per-env ECR repo, not per-app.** Storage waste + O(N) pushes made per-app wrong. `doh/{env_slug}/sidecar:{version}` is pushed once on the first sidecar-enabled deploy and reused by every sidecar'd app in the env. Keeps the existing "DOH customer-account ECR" pattern intact, no cross-account IAM gymnastics.
- **Fail-closed semantics.** Sidecar fails closed when PDP unreachable and cache miss; auth Lambda secrets read at cold start with warm-cache reuse; `rd` URL validated to require `https://` + the env's registrable parent domain. Session JWT TTL is 1 hour.
- **Fixed the per-app open-access policy collision.** `bootstrap_organization` previously seeded member/viewer wildcard `app:use` grants that would have over-allowed access to every PA in the org. Dropped those wildcards; `create_default_app_policy` skips the open-access grant when the template opts into the sidecar; PAs rely solely on the new global owner policy. Admins still have a wildcard override by design.
- **`username` locked post-creation.** Since `username` is the ABAC anchor for `$resource.owner`, letting it drift would enable takeover of owned-by resources. Override of `User.save()` raises on any post-insert change. Had to confirm no Django forms surface it (they don't — only display-only in personal settings).

### Architecture in one paragraph

On every request to `<slug>-hermes.<env-domain>`, the ALB host-header-routes to a two-container ECS task. The sidecar container owns the public port; the app container listens on `container_port + 1` internally. The sidecar checks the `doh_session` cookie (JWT verified against a cached JWKS from `auth.<env-domain>/.well-known/jwks.json`), redirects to the env's auth Lambda on `auth.<env-domain>/start` if absent/invalid, then POSTs `{app_id, oidc_sub, username, path}` to DOH's `/api/pdp/evaluate` with a bearer token. DOH runs `abac.evaluate_policies` with the new `$resource.owner` condition form and returns allow/deny. On allow, the sidecar injects `X-Auth-User`/`X-Auth-Sub`/`X-Auth-Email` and proxies to `127.0.0.1:<container_port>`. ALB health checks hit `/__sidecar/healthz` so probes succeed without cookies. Three per-env Secrets Manager entries back it: `devopshero/{env}/shared-secrets` (holds `DOH_SIDECAR_TOKEN`, hashed in the new `SidecarToken` DB row), `devopshero/{env}/sidecar-jwt-key` (RSA keypair for the session JWTs), `devopshero/{env}/oidc-config` (Okta config sourced from the parent Organization). Auth Lambda + sidecar ECR repo + image push all happen lazily on the first sidecar-enabled deploy per env.

### Non-obvious implementation details worth capturing

- **ALB listener rule priority for the auth Lambda is reserved at 10.** App rule priorities hash into 1000..41000 (`_compute_listener_rule_priority`), so anything under 1000 is safe. Pick 10, leave 1..9 for future infra rules. If any app ever starts hashing below 1000, that's a bug.
- **Sidecar health check path — `/__sidecar/healthz` — matters.** The ALB doesn't send cookies on health probes; if the target group's health path hit the app it would 302-to-auth and never go healthy. The sidecar handles this path locally in `sidecar/sidecar/app.py` before any cookie check.
- **`state` JWT is signed with the *same* keypair as session JWTs.** Keeps the Lambda to one signing key, no separate state-encryption secret. State TTL is 10 min, session TTL 1 hour.
- **PDP endpoint authenticates via `SidecarToken` row, not the `app_id`.** The token → env mapping is authoritative; `app_id` in the request body is self-reported but trusted because we're already inside a per-env trust boundary. DOH still verifies `app_id ∈ env` via the `DeploymentBlueprint` existence check to guard against typos/confused deputy within the env.
- **Lambda bundling.** Used `lambda_.Code.from_asset(..., bundling={"image": Runtime.PYTHON_3_12.bundling_image, ...})` so `cryptography` native wheels come from the Lambda-compatible image, not the developer's laptop. Verified synth in local CDK run.
- **Image pushed with `build_and_push_docker_image` from `ecr_utils.py`.** Reused unchanged. Sidecar source lives at repo-root `sidecar/` subdirectory; its `pyproject.toml` is separate from the main project's.
- **`sidecar_simulate` management command was invaluable.** Mints an ephemeral RSA keypair in-memory, signs a realistic session JWT, writes a `SidecarToken` row (replacing any existing one — can't recover the raw token once hashed), and POSTs to the live PDP. Three-way verification of owner-allow, non-owner-deny, admin-override took ~30 seconds against the live dev server. If we ever regret something about the decision pipeline, this is the first thing to reach for.
- **Existing `bootstrap_organization` test had to be updated** from "9 seed policies" to "8" because member/viewer `app:use` wildcards were removed. Any new org going forward has the PA owner policy pre-seeded.

### Risks and open items flagged for the pilot

- **ALB priority 10 has not yet been audited against existing hashed app priorities.** Worth a sanity check against Humanity Rules Sandbox state before first deploy.
- **Multi-env orgs will need `EnvironmentOIDCConfig`.** Deferred. Pilot envs are single-env.
- **`HERMES_WEBUI_PASSWORD` not removed.** Still present as a second auth path; disabling it is a follow-up.
- **Hermes WebUI header contract unspiked.** The sidecar injects `X-Auth-*` headers but we don't yet know if Hermes WebUI consumes them or needs a patch. User deferred this; deal with it when we first test the full UI.
- **JWKS cold start on fresh env.** First sidecar container in a new env may outrace the auth Lambda. Sidecar retries on unknown-kid + generous ECS health check grace should cover it; observe in practice.
- **Log volume.** `logger.info` per PDP decision will saturate CloudWatch under real use. Sample in v1.1 — not v1-blocking.

**Key points:**

- **Design → plan → code → verify, in that order.** Design doc (`docs/sidecar_proxy_design.md`) agreed with user first. Plan doc (`~/.claude/plans/sequential-hugging-crab.md`) broke it into 11 workstreams with file-level granularity. User approved the plan, then coding happened in tight commit cadence with tests first.
- **The `$resource.<key>` / `$identity.<key>` primitive is a small engine extension with large policy-model leverage.** One global policy now handles N personal assistants. Future owned-by patterns (personal notebooks, sandbox envs) fit the same form.
- **`bootstrap_organization` is now the single place that installs the PA policy** — new orgs get it automatically; existing orgs (like humr in dev) need a one-shot backfill. Did this manually during verification.
- **Per-env trust boundary was the load-bearing simplification.** Turned a per-app token/secret sprawl into three per-env Secrets Manager entries + one DB row per env. Kept the rest of the design approachable.
- **CDK synth passed locally with the Lambda bundling image actually running pip** — good signal the AWS deploy will work on first attempt, though real verification is pending Humanity Rules Sandbox deploy.
- **Session had ~311 total tests green at the end.** Every workstream landed with its own tests; the `sidecar_simulate` command gave an integration-test shaped feedback loop without needing AWS.

## 2026-04-18 19:59 - [Onboarding] End-to-end Okta OIDC test against existing Humanity Rules org

**Conversation:** [2026-04-18-1959-ee5cf47f.md](conversations/2026-04-18-1959-ee5cf47f.md)

First real end-to-end validation of the OIDC / Okta login path against a real Okta trial tenant, on local dev (SQLite). Goal wasn't to ship anything — it was to confirm the flow works and surface friction that the `docs/okta_oidc_setup.md` playbook doesn't cover. Ended with `vmendi@gmail.com` successfully logging in via Okta and landing on the existing superuser/admin row (keeping Humanity Rules admin + ABAC policies intact), while WorkOS login continues to work for the same account.

**What was exercised:**

- `setup_oidc_org --slug humr ...` flipped the existing WorkOS org to OIDC in place. The command is `update_or_create(slug=...)`, so reusing an already-bootstrapped slug just swaps `auth_provider` WorkOS → OIDC and stores creds. Bootstrap path doesn't re-run (correct — the org already has admin + seeded ABAC).
- Left `--bootstrap-admin-email` empty on purpose: the org is already bootstrapped, there's no meaningful "first admin" to seed.
- `/oidc/login/?org=humr` → Okta → `/oidc/callback/` flow works against a free Okta trial.

**Key learnings (what the playbook misses):**

- **`auth_provider` is not exclusive.** The field is only read in `oidc_login` (auth.py:83) to filter which orgs the OIDC entrypoint accepts. `auth_callback` (WorkOS) never reads it — it looks up purely by `workos_user_id`. So flipping an org to OIDC does *not* break WorkOS for users already linked to it. A user can effectively have two providers if both ID fields are populated on their row. Worth calling out in the doc — customers piloting Okta don't need a hard cutover.
- **Redirect URI on localhost.** The doc only shows the prod redirect (`https://devopshero.ai/oidc/callback/`). On the laptop it must be `http://127.0.0.1:8000/oidc/callback/` (or `localhost` — must match the browser host exactly, with trailing slash, http not https). Okta does exact-string matching and returned a clear "redirect_uri parameter must be a Login redirect URI" error.
- **Callback blows up on username collision** (auth.py:191). `oidc_callback` keys on `oidc_sub`, and when the lookup misses it unconditionally `create_user(username=email, ...)`. If a user with that email already exists (e.g. legacy WorkOS row), `username` UNIQUE fires. There is no email-fallback and no onboarding gate — unlike `auth_callback` which routes unknown WorkOS users through `/onboarding/`. For any future real customer piloting Okta whose employees' emails happen to collide with pre-existing DOH usernames, this will crash. Worth a proper fix: either an email-fallback link, or route to an onboarding page that merges identities.
- **`oidc_sub` wasn't exposed in Django admin.** `devopshero_app/admin.py:38-50` only surfaced `workos_user_id`. Added `oidc_sub` to `list_display`, `search_fields`, and both `fieldsets` / `add_fieldsets` ("OIDC" section). That gave a UI path to paste the sub onto the existing vmendi user row — after which retrying the login lands on the existing superuser and the callback's `update` path (auth.py:184-187) refreshes email/first/last from Okta.

**Resolution path for this specific test:**

1. Flip `humr` via `setup_oidc_org` (no `--bootstrap-admin-email`).
2. Add `http://127.0.0.1:8000/oidc/callback/` to Okta app's Sign-in redirect URIs.
3. Assign Okta user → app; ensure default authorization server has at least one access-policy rule (otherwise "Policy evaluation failed").
4. First login attempt to get `sub`… except there's no log for it, so either use a non-colliding email OR look up the Okta user's `00u…` ID in Okta admin and paste it into the now-visible `oidc_sub` field on the existing User row via Django admin.
5. Retry — works. Callback hits `User.objects.get(oidc_sub=...)`, refreshes name fields, logs in.

**Things worth doing next:**

- Log the `sub`/email in `oidc_callback` so operators can debug without digging in Okta.
- Add email-fallback (or onboarding route) for OIDC users whose email matches an existing user — right now a collision produces a 500 with no recovery path.
- Update `docs/okta_oidc_setup.md` with the localhost redirect URI note and with the "flipping an existing WorkOS org doesn't break WorkOS login" clarification.
- Consider a `link_oidc_sub` management command to formalize the pre-link workflow (vs. pasting in admin).

## 2026-04-18 19:40 - [Bugfix] Gate PostHog middleware on DEBUG to match client/context-processor

**Conversation:** [2026-04-18-1941-c4978add.md](conversations/2026-04-18-1941-c4978add.md)

A local OIDC-callback flow hit a legitimate `UNIQUE constraint failed` error on `User.username`, but Django's error page was being replaced by a second exception from PostHog: `ValueError: API key is required` raised from `posthog/__init__.py:850` inside `PosthogContextMiddleware.process_exception`. The middleware was trying to `capture_exception`, which lazy-calls `setup()`, which re-validates the API key and blows up if the default client was never initialized.

Root cause: inconsistent DEBUG-gating across the three PostHog integration points.

- `devopshero_app/apps.py:29` — client init: gated on `posthog_key and not settings.DEBUG`. ✅
- `devopshero_app/context_processors.py:10` — template config: gated on `not api_key or settings.DEBUG`. ✅
- `devopshero_site/settings.py:87` — middleware registration: **only** gated on `POSTHOG_API_KEY`. ❌

In local dev, `.env` has a real `POSTHOG_API_KEY` (so infra and prod work), so on dev boxes the middleware was being added to `MIDDLEWARE` but the default client was never set (because `apps.py` correctly skipped init in DEBUG). First unhandled exception → middleware tries to report it → PostHog's lazy `setup()` rechecks the module-level `api_key` var (unset) and raises. The secondary exception masks the real one in the debug page.

Fix: one-line change in `devopshero_site/settings.py:87` to `if POSTHOG_API_KEY and not DEBUG:`. All three sites now agree: PostHog is fully off in DEBUG.

**Key points:**

- The `process_exception` hook inside analytics middleware is a footgun: if it throws, it replaces the original traceback — exactly when you most need the original. Worth remembering if we ever add another exception-reporting middleware.
- PostHog's SDK has lazy module-level `setup()` that re-validates env at first call, *not* at process start. So "no errors at boot" doesn't prove the integration is wired up correctly — it only proves nothing has tried to use it yet.
- The pattern "guard every integration site on the same condition" is the right shape. The bug wasn't that DEBUG-gating was wrong conceptually — it was that one of three sites forgot to do it. If we ever add a fourth PostHog hook, it needs the same guard. Consider a single helper like `posthog_enabled()` in `settings.py` to collapse the three checks into one.
- Confirmed there's nothing else to gate: the infra stacks (`app_stack.py`, `cdn_stack.py`, `sync_secrets.py`) are CDK-only and don't execute in the Django runtime; the template partial `_posthog.html` already short-circuits on `{% if posthog_config_json %}` which the context processor sets to `None` in DEBUG.

## 2026-04-18 13:06 - [Deployment] Pin Hermes WebUI base image + verify 0.50.87 locally

**Conversation:** [2026-04-18-1307-62302632.md](conversations/2026-04-18-1307-62302632.md)

Pinned the Hermes WebUI base image from `:latest` to a specific version and verified the pin works end-to-end (build, boot, patches, chat). The path forward for rolling out new WebUI versions is now reproducible rather than drifting.

**Why pin:**

- `FROM ghcr.io/nesquena/hermes-webui:latest` in `template_repos/hermes_agent/Dockerfile:1` meant two customers redeploying on different days could land on different WebUI builds — no reproducibility.
- The builder EC2 doesn't pass `--pull` to `docker build` (see `ec2_builder_utils.py`), so long-lived builders would keep serving a stale cached `:latest` layer anyway.
- DOH stores only the app's ECR tag on `Deployment`, not the base image digest — we have no record of what WebUI a given customer is running.
- A pinned `FROM` tag turns "what webui?" into a grep of the Dockerfile and turns bumps into a git-visible action.

**Version landscape on GHCR at pin time:**

- Newest published tag: `0.50.87` (user picked it). Discovery: hit GHCR's v2 manifest API with a pull token to enumerate tags and resolve digests.
- `:latest` on GHCR was drifted — its digest matched an older build, not the newest semver. Another argument for pinning.

**Verification that 0.50.87 is a safe bump** (ran locally, not in a customer account):

1. **Build** — `docker build --pull -t hermes-agent-test:0.50.87 .` against the pinned Dockerfile. Clean.
2. **Boot** — ran with the local `.env` (Bedrock Haiku 4.5, us-east-1), `/health` returned `{"status":"ok"}` in ~8s.
3. **Patches** — all three applied fresh against the freshly-cloned upstream `hermes-agent`: `01-run_agent-bedrock-dots`, `02-run_agent-bedrock-caching`, `03-auxiliary_client-wire-bedrock`. Overlay file `agent/bedrock_aux_client.py` landed. `apply.py`'s `patch -N` dry-run strategy worked as intended.
4. **Caching patch (patch 02) specifically verified** — the whole point of this patch is the prompt-caching guard. Two checkpoints:
   - Both hunks present in patched `run_agent.py` at lines 885 and 1826: `provider in {"anthropic", "bedrock"}`.
   - Runtime dispatch in `hermes_cli/runtime_provider.py:914-918` shows that for `is_anthropic_bedrock_model(...)` (Claude-on-Bedrock), the `AIAgent` is instantiated with `api_mode="anthropic_messages"` and `provider="bedrock"` — exactly the combination patch 02 unlocks. `_use_prompt_caching` evaluates True.
5. **Chat round-trip** — created a session via `/api/session/new`, sent two turns via `/api/chat/start`, got expected responses from Bedrock Haiku ("PING-OK", "PONG-OK"). Token totals grew, session persisted.

**Odd thing worth flagging (not a blocker):** the `Server` HTTP header still reports `HermesWebUI/0.50.38` even though the image tag is `0.50.87`. Upstream's version string in the binary is likely hardcoded and not synced to the release tag. Something to ask upstream about if we start depending on that header for version detection.

**Docs updated so the pin + bump flow is discoverable:**

- `README.md` — added a "How to bump the WebUI base image" subsection with the 3-step flow (check GHCR tags → edit `FROM` → redeploy each customer app).
- `docs/personal_assistant_deployment_state.md` — added a known-constraint line noting the pin and that bumps require per-app redeploys.

**Rollout implications for existing customers:**

- Existing customer apps are still running whatever `:latest` resolved to at their last build. They won't pick up `0.50.87` until someone hits *Redeploy* on each one individually.
- There is no bulk "sync all hermes deployments" command yet. Two future options if that becomes painful: (a) a `manage.py redeploy_template_apps --template=hermes-personal` that loops Deployments, or (b) record `base_image_digest` on `Deployment` so we at least know who's drifted.

**Key points:**

- Pinning forces every bump to be a git-visible commit. No more silent drift.
- The in-repo `patches/` stack is robust across WebUI versions: `apply.py` uses `patch -N --dry-run` to distinguish "already applied" from "real failure," so once upstream ships a fix, the patch becomes a no-op on the next rebuild. Confirmed again here against the fresh `hermes-agent` clone in 0.50.87.
- Verification steps worth reusing on future bumps: (1) `docker build --pull`, (2) boot with local `.env`, (3) grep patched source for both caching hunks, (4) two-turn chat through `/api/session/new` + `/api/chat/start` + `/api/session?session_id=…`.
- GHCR tag discovery: `curl` the `v2/<repo>/tags/list` endpoint with an anonymous pull token (`ghcr.io/token?scope=repository:<repo>:pull`). Compare digests across tags via `HEAD /v2/<repo>/manifests/<tag>`.

## 2026-04-18 11:18 - [Deployment] Fix `patch` missing from hermes image + enable ECS deployment circuit breaker

**Conversation:** [2026-04-18-1119-62302632.md](conversations/2026-04-18-1119-62302632.md)

Two related changes from one debugging session on `hermes-slack07` (Humanity Rules Sandbox, default env), which had been stuck in `CREATE_IN_PROGRESS` on its `AWS::ECS::Service` for ~2 min and climbing when the user flagged it:

1. **Missing `patch` binary in the hermes-agent image.** The overlay/unified-diff refactor from earlier this morning added `apply.py`, which shells out to GNU `patch`. The base image `ghcr.io/nesquena/hermes-webui:latest` doesn't include `patch` — our Dockerfile only `apt-get install`s `git`. First container boot → `FileNotFoundError: [Errno 2] No such file or directory: 'patch'` from `subprocess.run` in `apply.py:61` → entrypoint exits non-zero (`set -e`) → task stops with `EssentialContainerExited`. ECS restarts it, same crash, forever. Fix: add `patch` to the apt-get list in `template_repos/hermes_agent/Dockerfile:6`. I missed it during verification because I ran apply.py on my Mac, where `patch` is part of the base system.

2. **Enabled the ECS deployment circuit breaker on customer app services.** Without it, a crash-looping task leaves the CFN stack waiting on `AWS::ECS::Service` for the full ~3h CFN stabilization timeout — `hermes-slack07` was on track for exactly that. Added `circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True)` to the `FargateService` in `devopshero_app/services/infra_customer/deploy_app.py:518`. Now the deployment trips after ~3-6 min on first-deploy failures.

**AWS doc deep-dive (verified with WebFetch, not memory):**

- **The circuit-breaker threshold is not user-configurable.** Formula: `threshold = ceil(0.5 * desired_count)`, clamped to `[3, 200]`. For `desired_count=1` this pins to the minimum of **3 consecutive failed task starts**, not the "~10" I initially said from memory. Quote from AWS: *"You cannot change either of the threshold values."*
- **Two-stage detection.** Stage 1 counts tasks that never reach `RUNNING`. Stage 2 (only entered if at least one task reaches `RUNNING`) counts health-check failures. Our crash happens in stage 1 — fast path.
- **`SERVICE_DEPLOYMENT_FAILED` EventBridge event is only emitted when the circuit breaker is on.** Without it, you don't get the failure signal at all — just CFN timing out hours later. This was a second reason to enable it beyond the time-saving.
- **Other loop-detection signals confirmed via docs** (for future reference, not enabled here):
  - `SERVICE_TASK_START_IMPAIRED` (WARN) — fires regardless of circuit breaker when the service consistently fails to start tasks. Good alerting target.
  - `SERVICE_TASK_PLACEMENT_FAILURE` / `SERVICE_TASK_CONFIGURATION_FAILURE` (ERROR) — scheduler-side failures (resources, IAM config).
  - `ECS Task State Change` events with `stopCode=EssentialContainerExited` — every individual crash, unconditionally.
- **Corrected a name I invented from memory:** `SERVICE_TASK_START_IMPOSSIBLE` does not exist. The real event is `SERVICE_TASK_START_IMPAIRED`.

**Rollback semantics on first deploy:** `rollback=True` auto-reverts to the prior `COMPLETED` deployment on trip. On a *first* deployment there is none, so the service deployment simply transitions to `FAILED` and the enclosing CloudFormation stack rolls back on its own — which is still a huge win vs. the 3h CFN timeout.

**Key points:**

- I verified the CDK API names (`ecs.DeploymentCircuitBreaker`, `FargateService.circuit_breaker` kwarg) by introspecting `aws_cdk.aws_ecs` in the project's venv before committing. Catching a typo here would have been a nasty second round of debugging.
- The fix to `deploy_app.py` applies to *new* app stack deployments. Existing CFN stacks don't pick it up until their next `cdk deploy` — every customer app that redeploys goes through this code path, so coverage is automatic over time.
- For the stuck `hermes-slack07` stack specifically, canceling/deleting the CFN stack manually is faster than waiting. It can then be re-created once the Dockerfile+`patch` fix is in the rebuilt image.
- The inline comment next to the circuit-breaker argument documents the 3-attempt threshold for `desired_count=1` — load-bearing context because someone reading the code later would not otherwise know the ceil/0.5/clamp formula.
- **Process learning:** when I claimed "~10 attempts" from memory, the user pushed back twice before I finally checked docs. Next time a factual claim about an AWS API / threshold / event name comes up, fetch the docs first. Memory-based claims about specific numeric thresholds or event names in AWS services are especially unreliable.

## 2026-04-18 09:59 - [Deployment] Restructure hermes-agent upstream patches as overlay + unified diffs

**Conversation:** [2026-04-18-1000-62302632.md](conversations/2026-04-18-1000-62302632.md)

Replaced the three inline Python-in-Bash heredoc patches in `template_repos/hermes_agent/entrypoint.sh` with a proper `patches/` directory: three unified-diff `.patch` files, one `overlay/agent/bedrock_aux_client.py` module, and an `apply.py` orchestrator. entrypoint.sh shrank from 366 lines to 106; the patch section is now a single `python3 /opt/hermes-defaults/patches/apply.py "$HERMES_DIR/hermes-agent"` call. Net behavior is identical — same three upstream bugs worked around (Bedrock dot-preservation in model IDs, Bedrock prompt caching, Bedrock aux client `aws_sdk` support).

**Why this structure specifically:**

- **Runtime patching, not build-time.** The obvious clean alternative — bake patched files into the image at build time — would silently stop fixing already-deployed EFS volumes. `$HERMES_DIR/hermes-agent` is copied from `/opt/hermes-defaults/` only on *first* boot (entrypoint.sh:70). All subsequent boots run against the EFS copy. If a new image ships a patch fix, it has to be applied on every boot against that existing EFS tree. This constraint is load-bearing and rules out the "just ship patched files" approach.
- **Unified diffs, not string replacement.** GNU `patch` with `-N --forward` gives us idempotency for free: already-applied patches are no-ops. The previous inline approach had to hand-roll "is this already patched?" string searches (e.g. `if patched in src:`) in each heredoc. `patch -N` handles it, and the stdout "Ignoring previously applied (or reversed) patch" message is what we detect to suppress false failures.
- **Overlay for new files.** `BedrockAuxiliaryClient` is ~70 lines of wrapper classes. Inline, it was a Python heredoc inside a Bash heredoc — no syntax highlighting, no linting, no way to test it independently. As a real `.py` file under `overlay/agent/`, it's a regular module. `apply.py` just `shutil.copy2`s it into place.
- **`patch -N --dry-run` followed by the real apply.** Dry-run lets me distinguish "already applied" (exit 1 + "previously applied" in stdout) from "real mismatch" (exit 1 + "FAILED" in stdout). First implementation matched on "Reversed (or previously applied)" — wrong phrase, GNU patch emits "Ignoring previously applied (or reversed) patch". Caught during the idempotency verification run.

**Verification:**

- Clean upstream clone → all 3 patches apply cleanly, overlay file copied, exit 0.
- Same tree, second run → all "already applied", exit 0 (idempotent).
- Corrupted anchor (renamed provider set) → exact patch name reported in stderr, exit 1 (fails loudly, not silently).
- AST-parsed all four touched Python files post-apply — no syntax errors.
- Every line the old inline patches touched was grep-confirmed to land at the same file:line after the new apply.py run.

**Key points:**

- Three `.patch` files use `-p1` style and `diff -u --label a/... --label b/...` headers, so they apply from the hermes-agent root with `patch -p1`.
- `apply.py` shells out to the system `patch` binary rather than pure-Python diff-apply (e.g. `whatthepatch`) to keep the orchestrator dependency-free. `patch` is already in the base image.
- `overlay/` is processed first, *then* patches. This matters because patch 03 imports `from agent.bedrock_aux_client` — the module has to exist before the patched `auxiliary_client.py` ever loads. (Import is at module scope, so it's evaluated on first import, not on first call to `resolve_provider_client`.)
- Patch 03 previously injected the Bedrock classes inline into `auxiliary_client.py`. Now it only adds an import, a 2-line `_to_async_client` hook, and a 20-line `aws_sdk` handler. Smaller diff surface = less likely to break on upstream drift.
- If upstream ships PR #11700, each `.patch` naturally becomes a no-op (already-applied) without any code changes on our side. The overlay file stays — it's harmless dead code if upstream provides its own `BedrockAuxiliaryClient`, and removing it would require the patch to *also* delete its import, which is more churn than the upstream-lands case is worth.

## 2026-04-17 15:22 - [Deployment] Expose SLACK_HOME_CHANNEL on hermes-slack template

**Conversation:** [2026-04-17-1524-042edb83.md](conversations/2026-04-17-1524-042edb83.md)

Added `SLACK_HOME_CHANNEL` as a user-editable runtime variable on the `hermes-slack` AppTemplate. This is an optional Slack channel ID (e.g. `C01234567890`) that the Hermes gateway uses as the destination for proactive messages: cron job results, scheduled messages, and other notifications that aren't a direct reply to a user. Upstream Hermes reads it from `~/.hermes/.env`; it wasn't wired through DOH, so there was no way to set it on a deployed `hermes-slack` app without shelling in.

**Design choices:**

- **`group="Slack"`, `user_editable=True`** — sits alongside the existing Slack vars (`SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_ALLOW_ALL_USERS`, `SLACK_ALLOWED_USERS`) in the deploy form's Slack group. Unlike those (kept hidden because tokens are secrets and allow-list is an admin concern), the home channel is something a workspace owner genuinely wants to pick per-deployment, so it's the first Slack var surfaced in the form.
- **Default value `"test-channel"`** — per user request. This is deliberately a placeholder that will fail politely at runtime (not a valid Slack channel ID), forcing the operator to replace it with a real `C0...` ID before cron jobs can deliver. An empty default would have been more "honest", but seeing the field populated in the form is a stronger prompt to fill it in.
- **Env-var passthrough only; no `config.yaml` template wiring** — Hermes reads `SLACK_HOME_CHANNEL` from `.env` directly via the gateway's Slack platform adapter, so the entrypoint just appends it to `$HERMES_DIR/.env` (same pattern as the four other `SLACK_*` vars at entrypoint.sh:360-364). No new placeholder in `config.yaml.template`.
- **Reseed-only change** — `runtime_variables` is a JSONField on AppTemplate, so `seed_app_templates` is idempotent and no migration is needed. Ran locally; prod seed is the rollout step.

**Key points:**

- Documented in upstream Hermes at `website/docs/user-guide/messaging/slack.md` ("Home Channel" section). There is **no upstream default** — if unset, Hermes has no home channel at all. `test-channel` is a DOH-side convention, not an upstream one.
- The companion var `SLACK_HOME_CHANNEL_NAME` (human-readable label) is NOT exposed. It's cosmetic and would just clutter the form.
- Bot must be `/invite`d to the channel for delivery to succeed. This is a runtime concern, not a config concern — the template can't enforce it.

## 2026-04-17 - [Hermes] Auxiliary LLM config: DOH_AUX_* vars, preseeded to Bedrock Sonnet 4.6

Added a shared auxiliary LLM configuration to `hermes_agent`. Hermes' `config.yaml` has eight auxiliary slots (`vision`, `web_extract`, `compression`, `session_search`, `skills_hub`, `approval`, `mcp`, `flush_memories`) that handle everything outside the main agent loop — memory flushing, web extraction, vision, etc. Until now they were all left as `provider: auto, model: ''`, which means Hermes silently falls back to the main model for every aux call. With Opus 4.6 as the default main, that's expensive for what are mostly cheap summarization/extraction calls.

**Design choices:**

- **One shared aux config, fanned out into all 8 slots**. Same provider/model/base_url substituted via `sed` into every slot. Simpler UX (one knob in the deploy form) at the cost of per-task tuning. If we later need (say) a vision-capable model separate from the compression model, we can split into groups — but YAGNI for now.
- **Reuse the main provider's API key, no separate `AUX_*` secret var**. In practice users will either run everything on Bedrock (AWS creds already there) or set one `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` shared across main+aux. A separate aux key would be paper-shuffling 99% of the time.
- **Preseeded defaults: `bedrock` + `us.anthropic.claude-sonnet-4-6`**. The main default is Bedrock Opus 4.6, and Sonnet 4.6 is the obvious cheaper-but-capable companion on the same provider. No AWS cred juggling, no second vendor relationship.
- **Defaults to main provider when unset**. `: "${DOH_AUX_PROVIDER:=$DOH_LLM_PROVIDER}"` — if someone deploys with `DOH_LLM_PROVIDER=custom` and doesn't touch the aux vars, aux inherits main. Sensible because provider mismatch is the most common misconfiguration (aux needs its own creds).
- **Bedrock base_url derived from region, same as main**. Unconditional overwrite if aux=bedrock (not a user-supplied fallback) — matches how `DOH_LLM_BASE_URL` is handled for the main path at entrypoint.sh:27. Fails fast if aux=bedrock but `AWS_BEDROCK_REGION` is unset.
- **Three vars user-editable in the deploy form** (`DOH_AUX_PROVIDER` / `DOH_AUX_MODEL` / `DOH_AUX_BASE_URL`), grouped under "Auxiliary LLM" alongside the existing "Main LLM" group. No new secrets exposed — all credential plumbing stays in the hidden `_HERMES_CREDENTIAL_VARS` / `_HERMES_BEDROCK_VARS` lists.

**Key points:**

- `config.yaml.template` substitutes three new placeholders (`__AUX_PROVIDER__` / `__AUX_MODEL__` / `__AUX_BASE_URL__`) in every auxiliary slot — the `api_key: ''` field stays empty because the auxiliary_client resolves the key from the provider config, not from the slot.
- Hermes' upstream auxiliary_client doesn't handle `auth_type == "aws_sdk"` (the Bedrock provider). The existing `BedrockAuxiliaryClient` patch in `entrypoint.sh` (injected in the 2026-04-15 Bedrock round) is what actually makes a Bedrock aux work — without it, configuring aux=bedrock would log `unhandled auth_type aws_sdk` and silently disable aux. The new vars assume that patch is in place.
- Reseed-only change — `runtime_variables` is a JSONField, no migration. `python manage.py seed_app_templates` on Opus's side and a redeploy on Hermes's side is the rollout.

## 2026-04-17 15:08 - [UI] Template deploy form: grouped variables, shared dropdown, light-mode fix

**Conversation:** [2026-04-17-1509-437e723e.md](conversations/2026-04-17-1509-437e723e.md)

Reworked `template_deploy_form.html` to stop wasting vertical space and use the project's standard dropdown component. Three intertwined changes: variables are now rendered inside collapsible groups; Workspace/Environment `<select>`s were replaced with the shared `_dropdown_select` partial; the partial itself was made light-mode-friendly.

**Design choices:**

- **Explicit `group` field on each variable, not derivation from `category`**. We already had `category` (`config`/`secret`) and could have grouped by that, but the user wanted semantic groupings under template-author control (e.g., "Main LLM" vs "Auxiliary LLM" — both `config`). Added `group: str` to every entry in `runtime_variables`. No migration: `runtime_variables` is a `JSONField`, so just reseed. The fallback when `group` is absent is `"General"`.
- **Group order = first-appearance order** in `runtime_variables`. Template authors already control the variable ordering in seed data; making group order derive from that is zero extra config. No separate `variable_groups` array with explicit ordering/descriptions — kept it minimal.
- **Expand groups that contain a required-and-empty variable; collapse the rest**. `_group_editable_variables` walks each var, builds groups (via `setdefault` to preserve insertion order), and sets `expanded=True` on any group that has `required=True` + empty `input_value`. This surfaces the fields the user *must* fill in, while keeping optional noise collapsed.
- **Rendered via native `<details>` / `<summary>`**, not Alpine. No JS for expand/collapse — browser handles state, the chevron rotates with `group-open:rotate-180`. Simpler than wiring `x-data={open}` just to toggle visibility.
- **Container width set to `max-w-3xl`** (user's final pick). Started at `max-w-2xl`, bumped to `max-w-5xl` to match `environment_detail.html`, but 5xl left the form feeling sparse at the top — 3xl (768px) is tighter and closer to a typical form width.
- **Switched Workspace/Environment `<select>`s to the shared dropdown partial** (`_dropdown_select.html`). The native chevron sits flush-right and felt cramped; more importantly, every other page uses the `el-select`/`el-options` Tailwind Plus Elements component. Diagnosed via grep that this was the only remaining raw `<select>` in the deploy flow (the other hit was `_people_attributes.html`, unrelated).
- **Made the shared dropdown partial light-mode-friendly**. It was authored dark-mode-first: `bg-white/5`, `text-white`, `outline-white/10` on the button, and `text-white` on option rows — unreadable in light mode. Changed to `bg-white dark:bg-white/5`, `text-gray-900 dark:text-white`, `outline-gray-300 dark:outline-white/10`, and option rows to `text-gray-900 dark:text-white focus:text-white` (so the indigo hover highlight stays legible). Touching this partial affected every page that includes it (sidebar org switcher, security people, policy editor) — verified by grep that all existing callers work in both themes.

**Key points:**

- View builds `workspace_options` / `environment_options` as `[{id, name}]` lists and resolves `selected_workspace_label` / `selected_environment_label` via a `_selected_label(options, value, placeholder)` helper. Needed because the partial expects a pre-rendered label string, not a value/options pair — `<el-select>` doesn't compute the displayed label on its own.
- Both GET and POST-with-errors paths were updated symmetrically. Forgetting the error path would have made the form re-render without dropdowns after a validation failure.
- Required-and-empty check for auto-expanding groups uses `var.get("input_value")` — on fresh GET this is the prefilled template value (so a required var with a sensible default stays collapsed), on POST-error it's the user's submitted value (so a group they tried to submit empty stays open showing the error context).
- The partial's existing callers all worked in dark mode; the light-mode fix was additive (dark variants preserved) so no regressions.

## 2026-04-17 12:02 - [DomainModel] Add user_editable to AppTemplate runtime variables

**Conversation:** [2026-04-17-1202-5f732343.md](conversations/2026-04-17-1202-5f732343.md)

Added a `user_editable` boolean to each entry in `AppTemplate.runtime_variables` so the template deploy form can expose only the subset of variables the user actually needs to supply (e.g., API keys, per-deploy overrides), while keeping infra-fixed values and auto-generated secrets hidden behind the template defaults.

Before this change the deploy form at `template_deploy_form.html` collected only App Name / Workspace / Environment and deployed using the template's stored `value`s verbatim — there was no way for a user to supply deployment-time values at all. This locked every API key and per-deploy credential to whatever was seeded, which is unusable for anything that varies per-tenant.

**Design choices:**

- **Name + default**: `user_editable` (boolean). Default `False` on every seeded variable. The user preferred an opt-in posture — safer default is "hidden", flip specific vars to `True` manually. Only `DOH_LLM_PROVIDER` / `DOH_LLM_MODEL` / `DOH_LLM_BASE_URL` on Hermes templates were flipped to `True` in this pass.
- **Override plumbing, not mutation**: the service accepts a `runtime_variable_overrides: dict[str, str] | None` and produces a new merged list via `_apply_variable_overrides`, rather than mutating `template.runtime_variables` in place. Templates are shared across deploys — mutating them would corrupt subsequent launches.
- **Secret inputs start blank, config inputs prefill**: secrets in the DB may hold a placeholder or a literal; echoing them back as the initial form value would either leak or mislead the user into thinking the placeholder is real. Config vars prefill from `value`, falling back to `default_value`.
- **Required validation reuses the existing `required` flag**: no new "required at deploy" axis. If a var is marked `required=True` and `user_editable=True`, the form enforces it at submit; otherwise the template's default flows through.
- **Error-path preservation**: on validation failure we re-render the form with the user's entered values in `input_value` so they don't lose typing — this matches the existing pattern for `app_name`.

**Key points:**

- The deploy view (`_handle_deploy`) reads `var_<NAME>` POST fields, builds an overrides dict containing only the non-empty submitted values, and forwards it to `deploy_from_template`. Empty submitted strings are dropped from the dict rather than overriding the template — this preserves the "None = auto-generate" semantics for secrets that are marked auto-generate but also happen to be user-editable.
- Input type is driven by `category`: `secret` → `<input type="password">` with `autocomplete="off"`, `config` → plain text. This is the only place where `category` affects UI today.
- Model docstring at `models.py:573` updated to list the new field, so readers of the JSON schema know it exists without grepping the seed file.
- `seed_app_templates.py` was intentionally modified after my pass to set the three Hermes LLM config vars to `user_editable: True` — everything else stays hidden by default.

## 2026-04-15 - [Hermes] Add Slack gateway, simplify LLM config vars

Added Slack integration support to the Hermes template. The upstream Hermes WebUI only serves the web interface — Slack/Discord/Telegram are handled by a separate gateway process (`python -m gateway.run`) that needs `slack-bolt` and `slack-sdk` installed.

**New file: `start_with_gateway.sh`** — orchestrates both processes. When Slack tokens are present, starts WebUI in background, waits for venv, installs Slack deps, waits for health check, then starts the gateway. If either process dies, the other is killed and ECS restarts the task. When no Slack tokens are set, falls through to WebUI-only mode.

**New file: `config.yaml.template`** — full Hermes config with `__CONFIG_PROVIDER__`, `__MODEL__`, `__BASE_URL__` placeholders, replacing the inline heredoc that was in the old entrypoint. Includes Slack platform toolsets, memory, compression, skills, and all auxiliary provider configs.

**Simplified LLM config vars** — renamed `HERMES_INFERENCE_PROVIDER` / `HERMES_MODEL` to `DOH_LLM_PROVIDER` / `DOH_LLM_MODEL` / `DOH_LLM_BASE_URL`. This eliminates a namespace collision where Hermes gateway's `resolve_runtime_provider()` reads `HERMES_INFERENCE_PROVIDER` from the environment and overrides `config.yaml`. With `DOH_*` namespacing, the entrypoint consumes these vars to generate config files but they never leak to child processes. Values are now Hermes-native (e.g. `custom` not `openai`), removing the provider translation logic and auto-detection. The entrypoint went from ~80 lines with conditionals to ~50 lines of straight passthrough.

**Key lessons:**
- Hermes doesn't recognize "openai" as a provider — direct OpenAI uses `provider: custom` with `base_url`.
- Model names with `/` (e.g. `openai/gpt-5.4-mini`) trigger OpenRouter routing even when not using OpenRouter.
- The gateway reads env vars before config.yaml, so any `HERMES_*` env var silently overrides config.
- `SLACK_ALLOW_ALL_USERS=true` must be in the process environment, not just `.env` — added to seed template as a config var so ECS injects it directly.

## 2026-04-15 15:00 - [Deployment] Fix hermes EFS crash: uid mismatch, consolidate efs_config

**Conversation:** [2026-04-15-1530-306a9944.md](conversations/2026-04-15-1530-306a9944.md)

Hermes-vmendi01 was crash-looping in Humanity Rules Sandbox. Diagnosed by assuming the customer role, finding the ECS task logs in CloudWatch. The upstream `hermeswebui_init.bash` script was failing at `sudo chown hermeswebui:hermeswebui /workspace` — "Operation not permitted".

Root cause: the EFS access point in `deploy_app.py` hardcoded uid/gid 1000, but the hermes-webui container runs as uid 1024. EFS access points map ALL operations (even root's via sudo) to the configured POSIX user. So `chown` to uid 1024 was rejected because the NFS server saw uid 1000 attempting a non-self ownership change.

The `/workspace` symlink from the previous session (pointing to EFS-backed `/home/hermeswebui/.hermes/workspace`) was correct in purpose but exposed the uid mismatch — the upstream init script chowns whatever `HERMES_WEBUI_DEFAULT_WORKSPACE` resolves to, and the symlink followed through to EFS where the chown failed.

**Key points:**

- **Consolidated `efs_mount_path` into `efs_config` JSONField** — Instead of adding separate `efs_posix_uid`/`efs_posix_gid` fields, replaced the CharField with a JSONField carrying `{mount_path, posix_uid, posix_gid}`. Follows the `datastore_config` pattern already in the codebase. OpenClaw gets uid 1000, hermes gets uid 1024.
- **EFS access points override ALL client identity** — Even `sudo chown` from inside the container goes through the access point's POSIX user. This means the access point uid MUST match what the container expects. A mismatch makes chown impossible regardless of container-side privileges.
- **Extracted the upstream `hermeswebui_init.bash`** — Pulled from `ghcr.io/nesquena/hermes-webui:latest` to read the actual script instead of guessing from log output. Confirmed the `sudo chown` on `HERMES_WEBUI_DEFAULT_WORKSPACE` is unconditional (runs whether env var was pre-set or defaulted).
- **Added `HERMES_WEBUI_DEFAULT_WORKSPACE` env var to Dockerfile** — Points directly to `/home/hermeswebui/.hermes/workspace`. Not strictly needed for the uid fix, but avoids the symlink indirection in the init script's chown path.
- **Redeployment requires production code update first** — The CDK runs from the production ECS container, so these changes must be pushed and deployed before tearing down and redeploying hermes-vmendi01. The CloudFormation stack (including the access point) needs full replacement.

## 2026-04-15 15:27 - [UI] Template picker cards: drop CPU, memory, port

**Conversation:** [2026-04-15-1527-7eb05c6e.md](conversations/2026-04-15-1527-7eb05c6e.md)

Simplified the "Deploy from Template" grid so each card shows only identity and narrative: icon, name, category pill, and description. Removed the footer row that listed CPU units, memory (MiB), and container port — those details are implementation noise on the chooser step and remain available later in the flow (e.g. configuration summary on the template deploy form).

**Key points:**

- **Picker vs. form** — The picker is for scanning templates; resource sizing belongs in deploy-time context, not repeated on every card.
- **Description retained** — Still the main signal for what each template is for.

## 2026-04-15 18:45 - [DevEx] Add --follow flag to doh_app_logs for live tailing

**Conversation:** [2026-04-15-1527-3ba70195.md](conversations/2026-04-15-1527-3ba70195.md)

Added `--follow` flag to the `doh_app_logs` management command so it can continuously poll CloudWatch for new log events, similar to `tail -f`. Also added `--follow-interval` to control the polling rate (defaults to 2 seconds). The initial batch of logs is printed first, then the command enters a polling loop using the `nextForwardToken` from the CloudWatch `get_log_events` API. Ctrl+C exits cleanly via KeyboardInterrupt handling.

Refactored `_fetch_and_print_logs` to return the forward token (was `-> None`, now `-> str | None`), and added a separate `_follow_logs` function for the polling loop. This keeps the one-shot fetch logic clean while adding the streaming capability as a separate concern.

**Key points:**

- **CloudWatch forward token enables follow** — `get_log_events` returns a `nextForwardToken` that acts as a cursor; passing it back in subsequent calls returns only newer events.
- **Clean separation** — `_fetch_and_print_logs` handles the initial batch and returns the token; `_follow_logs` handles the continuous polling loop. No mode-switching complexity in either function.
- **Motivated by previous session** — the `doh_app_logs` command was just built; during that work it became clear that a follow mode would be essential for debugging live/crash-looping apps.

## 2026-04-15 16:30 - [DevEx] Customer debugging skills and doh_app_logs command

**Conversation:** [2026-04-15-1523-e4c60e1e.md](conversations/2026-04-15-1523-e4c60e1e.md)

Retro on a previous session where debugging a crashing customer app (hermes in Humanity Rules Sandbox) was painfully slow — the agent wandered through architecture docs, tried prod-manage, and couldn't figure out how to access the customer's AWS account. Root cause: no documentation explaining the two-plane model (DOH control plane vs customer accounts), no "localhost by default" convention, and no command for fetching customer app logs.

Renamed `prod-debug` skill to `prod-controlplane-debug` with a clear disambiguation note — it's for DOH's own infrastructure only, not customer apps. Created a new `customer-debug` skill covering: localhost-by-default convention, the IAM role assumption pattern (`.env` credentials -> STS AssumeRole -> customer account session), pointers to `doh_query` and existing management commands, and an AWS CLI escape hatch for ad-hoc access. Added customer resource naming conventions (cluster, service, container, log group, log stream patterns).

Built `doh_app_logs` management command — the missing tool that would have made the original debugging session fast. Same argument pattern as `doh_app_shell` (`--account`, `--org`, `--env`, `--app`), handles the assume-role dance internally. By default tries running tasks first, then falls back to stopped/crashed tasks. Supports `--stopped` (skip to crashed tasks), `--head` (read from beginning), `--all` (paginate until exhausted), and `--limit`.

Tested in a fresh conversation: "Debug in customer's account Humanity Rules Sandbox the app hermes-vmendi00" — agent loaded `customer-debug`, loaded `manage-commands`, ran `doh_app_logs`, got logs, gave diagnosis. Straight shot, no wandering.

**Key points:**

- **Two-plane model was undocumented** — DOH control plane vs customer accounts. The agent had no way to know which plane to target. Now explicit in `customer-debug` skill.
- **"Localhost by default" convention** — unless user says "in production," use `uv run manage.py`. For production, swap to `./prod_manage.sh`. Same commands, same arguments.
- **Cross-account access was only visible in code** — the `.env` -> `settings.DOH_AWS_*` -> STS AssumeRole -> customer session chain was buried in `iam_utils`. Now documented in the skill, plus an AWS CLI recipe for ad-hoc access.
- **`doh_app_logs` fills the tooling gap** — `doh_app_shell` can't help when a task is crashed. `doh_app_logs` fetches CloudWatch logs for both running and stopped tasks.

## 2026-04-15 13:15 - [Deployment] Hermes EFS: fix mount path, /workspace symlink, remove cdk_stack_profile

**Conversation:** [2026-04-15-1414-e7c759d3.md](conversations/2026-04-15-1414-e7c759d3.md)

Deep session rethinking how EFS integrates with the Hermes container. Started with the observation that EFS was mounted at `/app/workspace` (the OpenClaw convention) but hermes stores everything under `/home/hermeswebui/.hermes`. The mount was useless — hermes state was ephemeral.

**What changed:**

Made the EFS container mount path configurable per template via a new `efs_mount_path` field on `AppTemplate` and `AppConfig`. OpenClaw keeps `/app/workspace`, hermes gets `/home/hermeswebui/.hermes`. The EFS decision in `deploy_app.py` now keys off `efs_mount_path` being set, which made `cdk_stack_profile` dead code — removed it from the model, AppConfig, admin, seed data, deploy form, config builder, and teardown executor.

Mounting EFS at the hermes home directory means the Dockerfile can't pre-populate it (EFS overlays at runtime). Moved the `hermes-agent` git clone to `/opt/hermes-defaults/` staging area; entrypoint copies it on first boot.

Discovered that hermes uses `/workspace` as its default working directory for terminal commands and file tools — which was NOT on EFS. Any files the agent created there were ephemeral. Fixed by replacing `/workspace` with a symlink to `/home/hermeswebui/.hermes/workspace` in the Dockerfile, and creating the target directory in the entrypoint.

Fixed a pre-existing bug: the `auto` -> concrete provider resolution was inside the `config.yaml` first-boot guard, so on second boot `PROVIDER` stayed as `"auto"` and `OPENAI_BASE_URL` was silently dropped from `.env`. Moved the resolution to run unconditionally.

**Key design decisions from the discussion:**

- **Hermes has a built-in `hermes update` command** — downloads latest agent framework while preserving user data. This means the `hermes-agent/` directory on EFS should be seeded once and never overwritten by us; the user manages framework updates.
- **Three categories of EFS content, all seed-once for different reasons** — config/SOUL.md (user-editable), hermes-agent (framework-managed via `hermes update`), skills/memory (runtime-generated).
- **WebUI and agent framework are independently versioned** — Docker image pins the WebUI binary; agent framework lives on EFS. Image bumps should be tested against existing EFS volumes.
- **`/workspace` symlink is the right approach** — catches all code paths (terminal, file browser, search_files) without needing to know every internal reference to `/workspace`. Created at build time as root; target directory created by entrypoint on boot.

Also added comprehensive documentation to `template_repos/hermes_agent/README.md` covering the storage architecture (what's ephemeral vs persistent, how EFS is mounted, the `/workspace` symlink, first boot vs reboot behavior, update strategy) and how environment variables flow from the DOH template system to the hermes container (config vs secret categories, Secrets Manager chain, entrypoint bridging to config.yaml and .env).

## 2026-04-15 12:25 - [DevEx] doh_app_shell — ECS Exec into deployed customer app tasks

**Conversation:** [2026-04-15-1225-f0367a63.md](conversations/2026-04-15-1225-f0367a63.md)

Fargate has no SSH; operators use **ECS Exec** (`aws ecs execute-command`) with the Session Manager plugin — SSM messaging under the hood, not a classic SSH port. For the DOH control plane, `infra_devopshero/prod_manage.sh` already wraps exec for `manage.py`; for raw shell, the repo documents `execute-command` against `doh-prod-cluster` / `doh-prod-app` / `devopshero`.

Customer apps use deterministic CDK names: cluster `devopshero-{env}-cluster`, ECS service `doh-{env}-{app_slug}`, container name equal to **app slug** (`AppStack` sets `container_name=app_config.app_name`). We added **`doh_app_shell`** so operators do not hand-assemble those strings: `--account` (and optional `--org` for disambiguation), `--env`, `--app` resolve `AWSAccount`, `Environment`, and `App` in Django, assume the customer role with `environment.aws_region`, list RUNNING tasks for the service, wait for RUNNING and `ExecuteCommandAgent`, then invoke `aws ecs execute-command` interactively (default command `/bin/bash`, overridable). Same retry pattern as `doh_efs_browse`’s exec helper. Documented in the manage-commands skill.

**Key points:**

- **Differs from `doh_efs_browse`** — Connects to an **existing** app task; does not register a temporary task definition or mount EFS root.
- **Validation** — Requires a matching `Environment` and `App` in the DB so typos fail before AWS calls.
- **Multi-task deployments** — Uses the first task ARN returned by `list_tasks` (order not guaranteed); document if we later add `--task-arn`.

## 2026-04-15 11:50 - [DevEx] doh_efs_browse: AL2023 plus selective dnf packages at task start

**Conversation:** [2026-04-15-1150-5158e361.md](conversations/2026-04-15-1150-5158e361.md)

The temporary Fargate task for `doh_efs_browse` uses `public.ecr.aws/amazonlinux/amazonlinux:2023`. That base image does not include vim, less, tree, and similar conveniences by default, so bare `sleep infinity` left ECS Exec sessions on mounted EFS awkward for inspection.

An earlier write-up here referred to `2023-full`; **that tag does not exist** on ECR Public for `amazonlinux/amazonlinux` (Fargate fails pulling the manifest). The documented family is the standard `2023` image versus `2023-minimal`, not a separate `2023-full`. A third-party troubleshooting image was also considered and rejected in favor of staying on the first-party AWS image.

**Decision:** Keep `amazonlinux:2023` and install a fixed tool set at container start, then `exec sleep infinity`: `dnf install -y vim-minimal less tree findutils tar gzip procps-ng`. That costs a short startup phase and needs outbound access to Amazon Linux package mirrors (usually via NAT in the VPC). The tool list lives explicitly in the management command.

**Key points:**

- **No fictional image tag** — Do not reference `2023-full`; validate tags against ECR Public before documenting or coding.
- **Runtime `dnf` vs external debug images** — Matches the chosen AL2023 base, avoids extra registries, and keeps operator tooling versioned in one place.

## 2026-04-15 00:30 - [Deployment] Fix hermes EFS mount path, remove cdk_stack_profile

**Conversation:** [2026-04-15-1414-e7c759d3.md](conversations/2026-04-15-1414-e7c759d3.md) (same session, continued above)

The EFS volume for hermes was mounted at `/app/workspace` (copied from OpenClaw's convention), but hermes stores all state under `/home/hermeswebui/.hermes` — config, conversations, webui data, skills, memory. The mount was useless; hermes state was ephemeral and lost on every ECS task replacement.

The fix makes the EFS container mount path configurable per app template via a new `efs_mount_path` field. OpenClaw keeps `/app/workspace`, hermes gets `/home/hermeswebui/.hermes`. The EFS decision in the CDK now keys off `efs_mount_path` being set, which made `cdk_stack_profile` dead weight — it was the only thing that field ever controlled — so we removed it entirely from the model, AppConfig, admin, seed data, and deploy form.

Mounting EFS at the hermes home directory means the Dockerfile can't pre-populate it (EFS overlays the directory at runtime). The hermes-agent git clone was moved to a staging location (`/opt/hermes-defaults/`) and the entrypoint copies it on first boot, matching the existing seed-once pattern for SOUL.md and config.yaml.

**Key points:**

- **`efs_mount_path` replaces `cdk_stack_profile` as the EFS trigger** — The profile field was introduced to select deployment patterns but only ever controlled EFS. A concrete path is more explicit: "mount EFS here" vs "use this abstract profile name."
- **Three categories of EFS content, all seed-once** — Config/SOUL.md (user-editable, don't overwrite customizations), hermes-agent framework (managed by `hermes update`, don't regress their version), skills/memory (runtime-generated, never touched by us).
- **Hermes has a built-in `hermes update` command** — It downloads the latest agent framework release while preserving user data. This means we should NOT overwrite the hermes-agent directory on reboot; the user or hermes manages its own framework version.
- **Provider auto-resolution bug fixed** — The `auto` -> concrete provider resolution (e.g. `auto` -> `openai`) was inside the config.yaml guard, so on second boot it was skipped. `PROVIDER` stayed as `"auto"` and `OPENAI_BASE_URL` was silently dropped from `.env`. Moved the resolution to run unconditionally.
- **WebUI vs agent framework are independently versioned** — The Docker image pins the WebUI binary; the agent framework version lives on EFS and is updated via `hermes update`. A WebUI image bump could theoretically require an agent framework update, but upstream claims backward compatibility.

## 2026-04-14 20:53 - [Deployment] Shared secrets per environment

**Conversation:** [2026-04-14-2054-879a27e5.md](conversations/2026-04-14-2054-879a27e5.md)

When deploying template apps (Hermes, OpenClaw), each new app gets its own Secrets Manager entry (`devopshero/{app-name}/secrets`) with empty placeholders for API keys like `OPENAI_API_KEY`, `TAVILY_API_KEY`, `SLACK_BOT_TOKEN`. Deploying N apps means filling the same keys N times in the AWS console.

**Solution:** A shared secrets store per environment — a single Secrets Manager entry at `devopshero/{env-slug}/shared-secrets` holds common API keys configured once. At deploy time, the control plane reads the shared secret and copies matching values into app-level secrets for any key that is an empty placeholder (`value=""`). The ECS task never knows shared secrets exist — it only reads from `devopshero/{app-name}/secrets` as before.

**Design discussion and key decisions:**
- Shared secrets are a **control-plane-only concept**. Values are copied into app-level secrets at `ensure_app_secrets_exist` time, before CDK runs. No IAM changes needed — neither for the task role (doesn't need to read shared secrets) nor the control plane (already has full Secrets Manager access via the assumed role).
- **Merge priority:** App-level always wins. If a template declares a literal value (like `HERMES_WEBUI_PASSWORD = "mysquirrel"`) or `auto_generate=True`, the shared secret is ignored for that key. Only empty-placeholder keys (`value=""`, `auto_generate=False`) are candidates for shared resolution.
- **Lazy creation:** The `devopshero/{env-slug}/shared-secrets` entry is created on first `shared-set` call, not during environment provisioning.
- The shared secret name is deterministic from `env_slug`, so no new Django model or migration is needed.
- `aws_account_id` is not unique at the DB level — two organizations can connect the same AWS account. Added `--org` (name or slug) to `doh_secrets` to disambiguate.

**Key points:**
- `secrets_utils.get_shared_secrets()` reads the shared secret, returning `{}` if it doesn't exist (fully backward compatible).
- `secrets_utils.ensure_app_secrets_exist()` now takes a `shared_secrets` parameter. A new `_resolve_secret_value()` function handles the three-way resolution: auto-generate for `None`, shared fallback for `""`, literal pass-through otherwise.
- `deploy_app.deploy()` calls `get_shared_secrets` before `ensure_app_secrets_exist` and passes the result through.
- `doh_secrets` management command gained `shared-list`, `shared-set`, `shared-delete` subcommands, plus `--org` on all subcommands.

## 2026-04-14 20:02 - [DevEx] doh_secrets — management command replaces secrets_utils CLI

**Conversation:** [2026-04-14-2002-ebec1f37.md](conversations/2026-04-14-2002-ebec1f37.md)

Customer Secrets Manager utilities already exposed `list_secrets` and `purge_deleted_secrets` (boto3 session in, metadata out; purge uses `DeleteSecret` with `ForceDeleteWithoutRecovery=True` for secrets already in scheduled-deletion state). The old `python -m devopshero_app.services.infra_customer.secrets_utils` entry point duplicated Django setup, `.env` loading, and account resolution.

**Decision:** Add `manage.py doh_secrets` with subcommands `list` and `purge-deleted`, matching patterns from `doh_efs_browse` (credentials from Django settings, `iam_utils.get_assumed_role_session`, connected `AWSAccount` only). Then remove `main()` and `__name__ == "__main__"` from `secrets_utils.py` so the management command is the single supported operator workflow. Documented the command in the manage-commands skill.

**Key points:**
- Purge only accelerates deletion for secrets already pending recovery-window deletion; it does not delete active secrets in one step.
- Region remains `us-east-1` in the command, consistent with the previous module CLI.

## 2026-04-14 19:20 - [DevEx] doh_efs_browse — ECS Exec timing fix and end-to-end verification

**Conversation:** [2026-04-14-1921-8e62912b.md](conversations/2026-04-14-1921-8e62912b.md)

Created a management command `doh_efs_browse` that spins up a temporary Fargate task with the root EFS volume mounted (no access point), then opens an interactive bash shell via ECS Exec. This gives full filesystem visibility into `/efs/deployments/<app-name>/` for any customer environment.

First attempt failed with `InvalidParameterException: The execute command failed because execute command was not enabled when the task was run or the execute command agent isn't running`. The task was RUNNING and `enableExecuteCommand=True` was set — the real issue was a **timing race**: the ECS Exec managed SSM agent (`ExecuteCommandAgent`) needs time to initialize after the task reaches RUNNING state.

**Fix:** Added `_wait_for_exec_agent()` which polls `describe_tasks` and checks `containers[].managedAgents[]` for the `ExecuteCommandAgent` to reach RUNNING status before attempting the connection. Also added retry logic (3 attempts, 10s apart) to `_exec_interactive()` as a safety net.

Verified end-to-end: task spins up, EFS mounts at `/efs`, `deployments/` directory visible (owned by UID 1000), interactive shell connects, and cleanup (stop task, deregister task def) runs on exit. The IAM role (`devopshero-{env}-efs-browser-role`) is created once and reused across invocations.

**Key points:**
- ECS Exec on Fargate requires: `enableExecuteCommand=True` on `run_task`, `initProcessEnabled: True` in container linuxParameters, and SSM permissions (`ssmmessages:*`) on the task role. The SSM agent is managed by the Fargate platform, not bundled with the container image.
- The `ExecuteCommandAgent` managed agent status is visible in `describe_tasks` response under `containers[].managedAgents[]`. Always wait for it to reach RUNNING before calling `execute-command`.
- Mounting EFS without an access point (root access) requires `elasticfilesystem:ClientRootAccess` in addition to `ClientMount` and `ClientWrite`. The task runs as root so it can see all app data regardless of which UID wrote it.
- The `--command` parameter of `aws ecs execute-command` does NOT interpret shell operators (`&&`, `>`, `|`). It passes the entire string as argv to the specified executable. Use `/bin/bash` as the command for interactive use.

## 2026-04-15 01:30 - [Deployment] Fix "auto" provider breaking Hermes onboarding skip

**Conversation:** [2026-04-14-1755-410755d0.md](conversations/2026-04-14-1755-410755d0.md) (continued)

First DOH deployment of Hermes hit the onboarding wizard despite `HERMES_WEBUI_SKIP_ONBOARDING=1`. The config had `provider: auto` because the AppTemplate defaults `HERMES_INFERENCE_PROVIDER` to `"auto"`. The WebUI's `_status_from_runtime` doesn't recognize "auto" as a provider — it's not in `_SUPPORTED_PROVIDER_SETUPS` (openrouter, anthropic, openai, custom) and it's not OAuth — so `provider_ready` is always False and `chat_ready` never becomes True.

Fixed by adding auto-detection in the entrypoint: when `HERMES_INFERENCE_PROVIDER=auto`, detect which API key is present (`OPENAI_API_KEY` → openai, `ANTHROPIC_API_KEY` → anthropic, `OPENROUTER_API_KEY` → openrouter) and resolve to that concrete provider before generating config.yaml. The existing `openai→custom` mapping then kicks in as before.

**Key points:**
- The WebUI and agent runtime have different provider taxonomies. The agent runtime understands "auto" (tries providers in priority order). The WebUI onboarding doesn't — it needs a concrete provider name to validate readiness.
- The entrypoint is now the bridge that translates between DOH's user-facing provider names and what the WebUI/agent actually need in config.yaml.
- Kept the AppTemplate default as "auto" rather than changing to "openai" — the entrypoint auto-detection is more robust since it handles any provider key the user configures.

## 2026-04-15 01:20 - [Deployment] Hermes WebUI password flow — tracing from seed to process env

**Conversation:** [2026-04-14-1820-082db88b.md](conversations/2026-04-14-1820-082db88b.md)

Investigated how `HERMES_WEBUI_PASSWORD` flows from DOH seed data to the running Hermes container. The question started as "is this how I set up the default password?" and led to tracing the full chain: seed data `value` field → `_materialize_app_secrets()` → Secrets Manager → ECS container env var → `os.getenv()` in the Hermes WebUI.

Read the upstream `api/auth.py` from `nesquena/hermes-webui` to confirm the WebUI reads the password directly from the **process environment** via `os.getenv('HERMES_WEBUI_PASSWORD')`, not from the `$HERMES_DIR/.env` file. This means the entrypoint doesn't need to write it to `.env` — that file is specifically for model provider API keys that the Hermes agent subprocess reads via dotenv.

Also clarified the three behaviors of the `value` field in `runtime_variables`: `None` = auto-generate at deploy time, `""` = empty placeholder, `"literal"` = use as-is. The `auto_generate` field in seed data is metadata only — not consumed by `template_deploy_service.py`.

**Key points:**
- The upstream Hermes WebUI uses `os.getenv()` for the password (priority: env var > `settings.json` hash) — no `.env` file involvement needed.
- The entrypoint's `.env` file serves a different purpose: writing API keys for the Hermes agent subprocess, which loads them via dotenv. The WebUI server reads its own config from process env and `settings.json`.
- Fixed the seed data: changed description from "auto-generated" to just "WebUI access password", set `auto_generate: False`, and hardcoded `value: "mysquirrel"` for all deployments.
- Added `chmod 600` on the generated `.env` file in entrypoint.sh since it contains API keys.

## 2026-04-15 01:10 - [Deployment] Tavily web search verification and config cleanup

**Conversation:** [2026-04-14-1755-410755d0.md](conversations/2026-04-14-1755-410755d0.md) (continued)

Verified that the Hermes Agent container uses Tavily for web search, and cleaned up dead config.

**Tavily verification:** Added `web.backend: tavily` to the generated `config.yaml` in `entrypoint.sh`. Without this, Hermes auto-detects the backend from whichever API key is present — explicit is better. Tested by sending "Search the web for: who won the 2026 Champions League final?" through the WebUI. The agent called `web_search`, got results from Wikipedia via Tavily, and answered correctly that the final is scheduled for 30 May 2026 in Budapest.

**Dead config.yaml removal:** The static `config.yaml` file in the template repo was vestigial — the entrypoint generates it dynamically from Docker env vars (with the `openai→custom` provider mapping). Deleted the file and removed its `COPY` line from the Dockerfile.

**EFS access point CDK fix:** `FileSystem.from_file_system_attributes()` returns an `IFileSystem` proxy that doesn't have `add_access_point()`. Switched to constructing `efs.AccessPoint()` directly. This affected all `fargate_web_efs` deployments, not just Hermes.

**Key points:**
- Hermes supports four web search backends: firecrawl (default), parallel, tavily, exa. Setting `web.backend` explicitly avoids surprises from auto-detection.
- The static config.yaml was a leftover from the initial scaffolding — once the entrypoint took over config generation, it became dead code that would never be read.
- CDK's `from_*_attributes()` methods return interface proxies with limited APIs — always check if the method you need exists on `IFileSystem` vs `FileSystem`.

## 2026-04-15 00:40 - [Deployment] Hermes Agent containerization — debugging the full startup chain

**Conversation:** [2026-04-14-1755-410755d0.md](conversations/2026-04-14-1755-410755d0.md)

Extended debugging session to get the Hermes Agent WebUI container fully working end-to-end: build, boot, skip onboarding, and chat with a model. The initial scaffolding (Dockerfile, entrypoint, config, AppTemplate seed) was created in the previous session — this session was about making it actually work through iterative testing against the real container.

**Container startup issues (resolved in order):**
- `USER root` left active — the init script (`hermeswebui_init.bash`) requires UID 1024 (`hermeswebui`). Added `USER hermeswebui` before ENTRYPOINT.
- `HERMES_WEBUI_STATE_DIR` missing — required by the init script but only set in the base image's docker-compose, not as a Dockerfile default. Added as `ENV`.
- Hermes Agent source not in image — the WebUI is just a frontend; it needs the agent source at `~/.hermes/hermes-agent/` to install deps from. Added `git clone --depth 1` during build.
- Health check on `/` returns 302 — the WebUI redirects to login. Switched to `/health` which returns 200.

**Onboarding wizard skip (the deep rabbit hole):**
- `HERMES_WEBUI_SKIP_ONBOARDING=1` exists but only fires when `chat_ready=True`.
- `chat_ready` requires: (1) agent installed and importable, (2) `model.provider` + `model.default` in config.yaml, (3) for "custom" provider: `model.base_url` set, (4) API key present in `~/.hermes/.env` FILE (not just Docker env vars).
- The entrypoint now generates both `config.yaml` and `~/.hermes/.env` to satisfy all conditions.

**Provider name mismatch (the other deep rabbit hole):**
- The WebUI's onboarding knows "openai" as a provider name and writes `provider: openai` to config.yaml.
- But the Hermes agent runtime does NOT recognize bare "openai" — it interprets it as an OpenRouter provider prefix and routes to `openrouter.ai/api/v1`, failing with 401.
- Direct OpenAI access uses the "custom" provider with `base_url: https://api.openai.com/v1`.
- The entrypoint maps `HERMES_INFERENCE_PROVIDER=openai` → `provider: custom` in the generated config.yaml.
- Verified by reading the agent's `auxiliary_client.py`: `resolve_provider_client()` has handlers for "auto", "openrouter", "nous", "openai-codex", "custom", and named API-key providers — but NOT "openai".

**Bug found via Codex review:**
- `PROVIDER` variable was assigned inside the first-boot `if [ ! -f config.yaml ]` block but used later in the `.env` generation. On subsequent boots with existing EFS config, `$PROVIDER` was empty. Moved assignment to top of script.

**Key points:**
- The Hermes ecosystem has a WebUI↔Agent impedance mismatch: the WebUI writes `provider: openai`, but the agent runtime rejects it. This is likely because the WebUI's onboarding was built for interactive users who then run `hermes model`, while the agent runtime has its own provider taxonomy.
- Three config surfaces need to be in sync: Docker env vars (for the process), `~/.hermes/.env` (for the WebUI's onboarding check), and `config.yaml` (for the agent runtime). The entrypoint bridges all three.
- Used Codex (GPT-5.4) via the ask-codex skill for independent verification. It confirmed the logic, found the `$PROVIDER` scoping bug, and recommended pinning image/agent versions for enterprise stability.

## 2026-04-14 17:31 - [Deployment] Add Hermes template, fix secret materialization, switch OpenClaw to Tavily

**Conversation:** [2026-04-14-1731-54d30bf5.md](conversations/2026-04-14-1731-54d30bf5.md)

Three related changes to the app template system:

**Hermes Agent template:** Added the second app template — Hermes Agent, an AI assistant with tool execution, persistent memory, self-improving skills, and multi-channel access (Slack, web). Created the full template repo (`template_repos/hermes_agent/`) with Dockerfile, config, entrypoint, SOUL.md, and README. Added `HERMES_TEMPLATE` to the seed command with runtime variables for inference provider, model selection, WebUI password (auto-generated), and optional API keys (Anthropic, OpenAI, OpenRouter, Tavily, Slack).

**Secret materialization fix:** `_materialize_app_secrets` was skipping any secret with `value: ""`, which meant optional secrets like `OPENAI_API_KEY` never made it into Secrets Manager — users had no way to fill them in post-deployment. Removed the empty-string skip so all template-defined secrets are provisioned: `None` = auto-generate random token, `""` = empty placeholder the user fills in later, `"literal"` = use as-is. This is better UX because all available secrets are visible in Secrets Manager from day one.

**OpenClaw Brave→Tavily migration:** Switched the OpenClaw template's web search provider from Brave to Tavily. Updated `openclaw.json` (search provider and plugin config), `.env.example`, and the seed command (`BRAVE_API_KEY` → `TAVILY_API_KEY`).

**Key points:**
- Secret materialization was the key insight: the old `value: ""` → skip logic meant optional secrets were invisible to users post-deployment, with no way to configure them in Secrets Manager
- The fix is backward-compatible: existing deployments keep their current secrets, new deployments get all template secrets provisioned
- For existing deployments that need new secrets, users must either redeploy from template or manually add keys to the SM entry at `devopshero/{app-name}/secrets`

## 2026-04-14 16:02 - [Deployment] Add EFS persistent storage for AI agent workspaces

**Conversation:** [2026-04-14-1603-b5ee55d1.md](conversations/2026-04-14-1603-b5ee55d1.md)

OpenClaw agents store all state as files under `/app/workspace/` — session memory, runtime metadata, SQLite databases. On plain Fargate this is ephemeral: every container replacement (deploy, crash, scale event) wipes the agent's memory. We needed persistent storage that requires zero changes to OpenClaw itself.

Evaluated five options: EFS, Fargate ephemeral storage (not persistent), Aurora Serverless (wrong model — OpenClaw is file-based, not SQL), S3 with FUSE (requires SYS_ADMIN capability, high latency), DynamoDB (same mismatch as Aurora). EFS was the clear winner: mount it at `/app/workspace` and OpenClaw writes files as normal without knowing the difference. DOH's own control plane already uses this exact pattern for Claude session persistence (`storage_stack.py` + `app_stack.py`).

Architecture: one EFS filesystem per customer environment (created in `deploy_base`, shared infrastructure, effectively $0 cost with no data stored), one access point per agent deployment (created in `deploy_app`, path-isolated at `/deployments/{app-name}` with UID 1000 enforcement). This gives per-agent isolation at the EFS layer — agents can't see each other's files even though they share a filesystem. EFS supports up to 1,000 access points per filesystem. Using `app_name` (app slug) for the path gives human-readable browsability and a nice recovery property: tearing down and redeploying with the same name automatically recovers the previous workspace data.

The implementation activates the existing `AppTemplate.cdk_stack_profile` field, which was defined but never consumed by the deployment pipeline. The OpenClaw template now uses `fargate_web_efs` instead of `fargate_web`. When `deploy_app.AppStack` sees this profile, it imports the shared EFS, creates a per-app access point, adds the EFS volume to the task definition, mounts it at `/app/workspace`, and grants scoped IAM permissions (ClientMount + ClientWrite, conditioned on the access point ARN).

**Key points:**
- **Why EFS over alternatives** — Only option requiring zero OpenClaw code changes; file-based memory model maps directly to a mounted filesystem
- **Cost** — Effectively free for agent workspaces (text files, KBs to low MBs). EFS Standard $0.30/GB-month, Infrequent Access $0.025/GB-month. No provisioned throughput needed.
- **Per-agent isolation** — EFS access points enforce path + UID isolation (`/deployments/{app-name}`, UID 1000/GID 1000). Chroot-like — each agent sees only its own directory.
- **`cdk_stack_profile` activated** — Field existed on `AppTemplate` but was never read by deployment code. Now `deploy_app` dispatches on it: `fargate_web` (existing behavior) vs `fargate_web_efs` (adds EFS volume + mount)
- **`AppConfig` extended** — New `cdk_stack_profile` field (defaults to `"fargate_web"`), threaded from `App.source_template.cdk_stack_profile` through `app_config_builder` and `teardown_executor`
- **Recovery semantics** — Redeploying an agent with the same name reconnects to existing workspace data (access point recreated at same path). Clean slate requires deleting the EFS directory or using a new name.
- **Container UID** — OpenClaw image runs as `node` (UID 1000), confirmed from Dockerfile `COPY --chown=node:node`. Access point enforces matching UID/GID.

## 2026-04-14 22:38 - [Deployment] Fix OpenClaw "origin not allowed" error — add Host header origin fallback

**Conversation:** [2026-04-14-1338-4bb16423.md](conversations/2026-04-14-1338-4bb16423.md)

After deploying OpenClaw via DOH to `https://ai-assistant-openclaw.chsandbox.com/`, the Control UI loaded but displayed "origin not allowed (open the Control UI from the gateway host or allow it in gateway.controlUi.allowedOrigins)". The OpenClaw gateway validates the browser's `Origin` header against an allowlist, and the existing `controlUi` config only had `dangerouslyDisableDeviceAuth` and `allowInsecureAuth` — no `allowedOrigins`. By default, OpenClaw only permits connections from `localhost`/`127.0.0.1`, so the HTTPS domain was rejected.

Three options were considered: (A) explicit `allowedOrigins` with the exact domain, (B) `dangerouslyAllowHostHeaderOriginFallback: true` to trust the Host header, (C) wildcard `"*"`. Chose option B because the app sits behind DOH's ALB which controls the Host header, making it reasonably safe, and it works regardless of what domain DOH assigns without needing config changes per deployment.

Note: the fix was applied to `template_repos/openclaw_agent/openclaw.json` (the DOH template), not to `deployable-repos/` (the deployed copy). The initial edit was mistakenly made to the wrong location.

**Key points:**
- **Root cause** — OpenClaw gateway origin validation rejects any origin not in `controlUi.allowedOrigins`; defaults to localhost-only when unset
- **Fix** — Added `dangerouslyAllowHostHeaderOriginFallback: true` to `gateway.controlUi` in `openclaw.json`
- **Why Host header fallback over explicit origins** — Works for any DOH-assigned domain without per-deployment config changes; safe behind ALB
- **Known upstream bug** — OpenClaw CLI commands that touch `openclaw.json` can reset `allowedOrigins` to defaults (issue #49950), but since the config is baked into the Docker image and not modified at runtime, this doesn't affect DOH deployments

## 2026-04-14 20:46 - [Deployment] Eliminate desired_count=0 workaround — two-phase CDK deployment

**Conversation:** [2026-04-14-1147-17ecc887.md](conversations/2026-04-14-1147-17ecc887.md)

The ECS service in `AppStack` was created with `desired_count=0` and then scaled up to 1 via `ecs_utils.start_ecs_service()` after the Docker image was pushed. This was a remnant from when the ECR repository lived inside the same stack as the ECS service — at that time you couldn't push the image before the stack deployed (no repo yet), but you also couldn't start tasks during the deploy (no image yet). Starting at 0 and scaling up after the push was the only workaround.

Since `EcrStack` is now an independent stack deployed before `AppStack`, the workaround is no longer needed. Refactored `cdk_utils.py` to support deploying specific stacks from a pre-synthesized assembly, then restructured `deploy()` into three phases: (1) deploy ECR + Aurora stacks, (2) build & push the Docker image, (3) deploy the App stack with `desired_count=1`. ECS now pulls the image immediately during the CloudFormation deploy.

The `start_ecs_service` / `wait_for_service_stable` functions in `ecs_utils.py` are no longer called during deployment. CloudFormation handles ECS service stabilization natively. There's a known trade-off: we lose task-level failure diagnostics (container crash reasons) and the fast early-abort behavior — CloudFormation's error messages are more generic. On first deploys, a crashing container causes CloudFormation to roll back the entire App stack, whereas the old flow left the stack at `desired_count=0` for investigation. On re-deploys, CloudFormation rolls back to the previous working version, which is acceptable. The diagnostics trade-off is documented but accepted for now in favor of a simpler deploy pipeline.

**Key points:**
- **Root cause of the workaround** — ECR repo used to be in the same stack as the ECS service; `desired_count=0` prevented ECS from pulling a non-existent image during initial deploy
- **`cdk_utils.py` refactored** — Split into `synth_cdk_app()`, `deploy_from_assembly()`, and `deploy_cdk_stacks()` (convenience wrapper for `deploy_base.py`)
- **`deploy_app.deploy()` now three-phase** — ECR/Aurora first → image push → App stack with `desired_count=1`
- **`start_ecs_service` preserved but unused** — Kept in `ecs_utils.py` for potential operational use (manual restarts, retry scenarios)
- **Trade-off accepted** — Lost task-level crash diagnostics in favor of simpler flow; CloudFormation handles stabilization

## 2026-04-14 10:56 - [Bugfix] Fix deployed environment row showing failed redeploy instead of last successful deployment

**Conversation:** [2026-04-14-1056-f8635f89.md](conversations/2026-04-14-1056-f8635f89.md)

The `current_deployment_id_subquery` in `_build_deployed_environment_rows` ordered deployments purely by `-created_at`, so a failed redeploy attempt created after a successful deployment would become the "current" deployment shown in the environment row. This was misleading — the row should show what's actually running, not the latest failed attempt.

Fixed by adding a `Case/When` priority ordering to the subquery: in-progress deployments sort first (priority 0), then SUCCEEDED (priority 1), then everything else (priority 2). Within the same priority, most recent first. This ensures active redeploys are visible while failed attempts don't mask the last successful deployment.

The previous journal entry noted this as a pre-existing test failure (`test_app_detail_deployed_environments_uses_latest_launched_blueprint_per_environment`) — this fix resolves it.

**Key points:**
- **Root cause** — Subquery used `order_by("-created_at")` with no status awareness, so any VISIBLE deployment could become "current" based solely on creation time
- **Fix** — Added `Case/When` annotation for `status_priority` to both `current_deployment_id_subquery` and `current_deployment_created_at_subquery`, ordering by priority first, then recency
- **Both redeploy tests pass** — The in-progress redeploy test still works (PENDING has priority 0), and the failed redeploy test now correctly returns the SUCCEEDED deployment

## 2026-04-14 10:36 - [UI] Fix expandable config state reset on app detail page — per-row polling

**Conversation:** [2026-04-14-1036-e503ca26.md](conversations/2026-04-14-1036-e503ca26.md)

The app detail page had a global `#main-content` HTMX refresh that violated the UI live update contract. When a deployment was in progress or tearing down, the entire page polled every 10s, destroying all Alpine.js state — most visibly, the expandable "Config" chevron on blueprint rows would collapse back to closed on every poll cycle.

The fix restructures the page from one global poll to per-row self-terminating polls. Each blueprint row is now an independent polling widget: the `<tr x-data="{ open: false }">` is a stable wrapper that HTMX never replaces, and only an inner `<div>` gets swapped via `innerHTML`. Alpine's MutationObserver re-binds directives (`@click`, `x-show`) to the surviving `x-data` scope, so `open` retains its value across polls.

The template uses Django 6.0 `partialdef` so the same template serves both initial render (full `<tr>` via `{% include %}`) and polling responses (just the inner content via `#blueprint_row_content` partial rendering).

**Key points:**
- **Contract violation fixed** — Moved from `hx-target="#main-content"` (global) to `hx-target="#blueprint-inner-{{ id }}"` (per-row). Only rows in transient states poll; terminal states render without polling attributes, so polling self-terminates.
- **Status logic consolidated on the model** — Added `Deployment.IN_PROGRESS_STATUSES`, `CONCLUDED_STATUSES`, `VISIBLE_STATUSES`, `TRANSIENT_STATUSES` as class-level tuples, plus `is_in_progress`, `is_concluded`, `is_transient` properties. Removed the duplicate view-level tuples and hardcoded status strings in templates.
- **Table layout trade-off** — Moved from 4 `<td>` columns to a single `<td>` with inner flex layout. The table was already visual-only (sr-only header, no data-table semantics), so the trade-off is minimal. This avoids OOB swap complexity.
- **`should_auto_refresh` removed** — The top-level context variable is no longer needed since each row controls its own polling.
- **Pre-existing test failure noted** — `test_app_detail_deployed_environments_uses_latest_launched_blueprint_per_environment` fails on main too (timestamp ordering issue with `auto_now_add` in tests). Not related to this change.

## 2026-04-13 23:14 - [Deployment] Per-app health check grace period for slow-starting containers

**Conversation:** [2026-04-13-2314-9e26afad.md](conversations/2026-04-13-2314-9e26afad.md)

Diagnosed another OpenClaw deployment failure — same root cause family as the previous session but different mechanism. This time the container was starting correctly (gateway reported "ready" after ~6s), but the ECS task itself took ~50s from creation to HTTP-ready (image pull + container boot + gateway startup). With `health_check_grace_period=0` in DEBUG mode, ALB started health-checking immediately on target registration. Three consecutive "Request timed out" failures (5s interval × 3 = 15s) caused ECS to kill the task before it could serve traffic. ECS cycled through tasks, and DOH's 180s stabilization timeout expired.

The previous session's fix (setting `health_check_grace=60` for production) didn't apply to DEBUG mode, which intentionally uses `grace=0` for fast iteration on quick-starting apps. Rather than raising the global DEBUG grace period (which would slow down feedback for most apps), added a per-app `health_check_grace_period` override that flows through the full stack: `AppTemplate` → `App` model → `AppConfig` dataclass → CDK `AppStack`. When set (non-zero on model, non-None on AppConfig), it overrides the environment default. OpenClaw template now specifies 120s.

**Key points:**
- ECS task lifecycle: task creation → image pull → container boot → app startup → HTTP ready. For OpenClaw this total is ~50s, far exceeding the 15s (3 × 5s) unhealthy threshold with zero grace.
- `health_check_grace_period=0` on both models means "use environment default" — no behavior change for existing apps. Only templates/apps that explicitly set it get the override.
- Debugging approach: assumed role into customer AWS account (Humanity Rules Sandbox 266117665083) via the stored `role_arn`/`external_id` on `AWSAccount` model, then inspected ECS events, stopped task descriptions, and CloudWatch container logs to reconstruct the timeline.

## 2026-04-14 05:30 - [Deployment] Debug OpenClaw deployment failure, fix failed/torn-down app lifecycle

**Conversation:** [2026-04-13-2314-5af1a128.md](conversations/2026-04-13-2314-5af1a128.md)

Diagnosed and fixed the first OpenClaw template deployment failure, then fixed several lifecycle gaps exposed by the failure.

**Deployment diagnosis:** The OpenClaw ECS service failed to stabilize within DOH's 180s timeout. Root cause was the model configuration — `OPENCLAW_DEFAULT_MODEL=claude-sonnet-4-20250514` (bare name) caused OpenClaw to fall back to `openai/claude-sonnet-4-20250514`, an unknown model. The SDK model warmup then blocked the Node.js event loop for ~40 seconds waiting for a timeout, during which the `/health` HTTP endpoint couldn't respond at all. ALB health checks (2s timeout) saw "Request timed out", ECS cycled tasks, and DOH gave up at 180s — literally 1 second before the deployment actually completed (`rolloutState=COMPLETED` at 20:31:06, DOH timed out at 20:31:05). The fix: change the model to `openai/gpt-5.4-nano` (provider-qualified name) so warmup succeeds in seconds instead of blocking for 40s. Also added `health_check_grace_period` (120s for OpenClaw) as a per-app override on the ECS service, propagated through `AppTemplate` → `App` → `AppConfig` → CDK `AppStack`.

**Failed deployments invisible in UI:** After the deployment failed, the app appeared in "Recent Deployments" with a failed badge but was missing from "Deployed to Environments" — despite consuming real ECS/ALB resources. The status filter (`CURRENT_LIVE_DEPLOYMENT_STATUSES`) only included success/in-progress states. Renamed to `VISIBLE_DEPLOYMENT_STATUSES` and `CONCLUDED_DEPLOYMENT_STATUSES` (replacing `CURRENT_LIVE`/`CURRENT_LAUNCHED`), adding `FAILED` and `TORN_DOWN`. Failed and torn-down deployments now appear with appropriate actions.

**No retry/teardown path for failed deployments:** The "Redeploy" button, "Tear Down" button, and their backend views were all gated on `status == 'succeeded'`. Extended both view guards and the template condition to accept `failed` (and `torn_down` for redeploy). Permissions editor stays succeeded-only since it requires a running app.

**Blueprint discarded on teardown killed redeploy path:** The teardown executor set the blueprint to `DISCARDED`, which excluded it from all queries. After teardown, the app vanished from "Deployed to Environments" with no way to redeploy. Analysis showed keeping the blueprint `ACTIVE` after teardown has no side effects — `ACTIVE` is not in `OPEN_BLUEPRINT_STATUSES` (won't interfere with new drafts) and not in `DISCARDABLE_BLUEPRINT_STATUSES` (won't be accidentally discarded). Removed the `DISCARDED` transition from the teardown executor. `DISCARDED` now means only "abandoned draft," matching its original intent.

**Key points:**
- **Model warmup blocks event loop** — OpenClaw's Node.js event loop is completely blocked during model warmup. Wrong model name → 40s timeout → `/health` can't respond → ALB marks unhealthy. Correct provider-qualified name makes warmup succeed in ~2s.
- **`CONCLUDED_DEPLOYMENT_STATUSES`** — New name for the "pipeline finished" statuses (SUCCEEDED, FAILED, TEARDOWN_PENDING, TEARING_DOWN, TORN_DOWN). Used to pick which blueprint is "current" per environment.
- **`VISIBLE_DEPLOYMENT_STATUSES`** — In-progress + concluded. Used to find the deployment to display in environment rows.
- **Blueprint stays ACTIVE through teardown** — Config is preserved and reusable. Deployment's TORN_DOWN status conveys the infrastructure state.
- **Blueprint config is a snapshot** — Updating the template doesn't propagate to existing blueprints. Manual DB fix needed for now; blueprint env var editing is a future feature.

## 2026-04-14 00:01 - [UI] Add "Tear Down" button to environment detail page

**Conversation:** [2026-04-13-2101-62d8ea41.md](conversations/2026-04-13-2101-62d8ea41.md)

Added a UI-triggered environment teardown flow to the environment detail page, matching the existing patterns for destructive actions in the system (group delete, policy delete, app deployment teardown).

The button is a red outlined style consistent with the "Delete Group" button in security views, placed in the header bar next to the existing "Resume Setup" button. It only appears for org admins when the environment status is `ready` or `error` — these are the states where teardown makes practical sense. The agent tool (`teardown_environment`) is more permissive (allows any status except already-tearing-down), but the UI intentionally limits to the two most relevant states.

The flow uses the standard HTMX confirmation modal pattern: button issues `hx-get` to load `_confirm_modal.html` into `#modal-container`, the confirm button in the modal issues `hx-post` to the teardown endpoint, which sets the environment to `TEARDOWN_PENDING` and re-renders the detail page (showing the updated status pill). The job worker picks up `TEARDOWN_PENDING` environments for actual infrastructure destruction.

The POST view sets the status directly (same pattern as `app_deployment_teardown`) rather than calling the async `teardown_environment` agent tool — keeps the view synchronous and avoids unnecessary indirection. Both views are protected by `environment:admin` ABAC, with an additional `user_is_org_admin` template guard on button visibility.

**Key points:**
- **Status gating** — Button visible only for `ready` and `error` environments; the POST also validates this server-side (returns 422 otherwise)
- **Two-layer auth** — Template hides button via `user_is_org_admin`; both GET (confirm) and POST (teardown) enforce `environment:admin` ABAC
- **Reuses `_confirm_modal.html`** — No new template needed; the shared confirmation modal handles title, message, and confirm action via context variables

## 2026-04-13 22:30 - [Deployment] App Templates Phase 1: design and implement one-click deploy from bundled templates

**Conversation:** [2026-04-13-2056-ae606152.md](conversations/2026-04-13-2056-ae606152.md)

Designed and implemented the "App Templates" system (Phase 1) for DOH. Templates are pre-configured application recipes that let anyone deploy a governed app with a single form submission — no agent conversation, no infrastructure knowledge required. The first template is the OpenClaw AI Assistant, proving the end-to-end flow from template selection to deployment.

The design session covered several key decisions that shaped the architecture:

**Bundled template repositories (`file://` mechanism):** Template source code lives inside DOH's own source tree under `template_repos/`. This avoids the complexity of external Git repos, S3 buckets, or trying to create repos in the customer's GitHub org. The existing `repo_service.clone_repository` already supports `file://` URLs via `shutil.copytree`, so no changes were needed in the clone path. The `TEMPLATE_REPOS_DIR` setting resolves to `BASE_DIR / "template_repos"`, which works in both local dev and the production ECS container (where the code is baked into the Docker image).

**`AppTemplate` model design:** The model captures everything needed to deploy an app: container settings (port, CPU, memory, Dockerfile path), infrastructure profile (`cdk_stack_profile`), and a `runtime_variables` JSON field. Several fields were explicitly dropped during design: `source_repo_branch` (dead field for local file repos) and `repo_subpath` (templates are self-contained). The model is platform-global — ABAC filtering happens at the workspace/environment level in the deploy form, not at the template picker.

**`runtime_variables` schema:** Each variable is a dict with `name`, `category` (config vs secret), `description`, `required`, `auto_generate`, `default_value`, and `value`. The materialization logic splits these into two paths: `config` variables become `DeploymentBlueprint.environment_variables`, while `secret` variables become `DeploymentBlueprint.app_secrets` (with `auto_generate=true` producing `None` values that the deployment pipeline fills, and empty-string values being omitted). All metadata fields were included from the start to avoid future migrations.

**Deployment flow (direct, no agent):** User picks a template → fills a form (app name with sensible default, workspace dropdown, environment dropdown) → submits → `deploy_from_template` service creates Repository, App, DeploymentBlueprint, and Deployment records in one transaction → redirects to app detail. The workspace dropdown is filtered by `workspace:edit` ABAC, environments by `environment:deploy` ABAC with `status=READY`. Multiple instances of the same template per org are supported (each gets its own app name/slug).

**Template-to-App traceability:** A nullable `source_template` ForeignKey on `App` points back to `AppTemplate`, enabling queries like "which apps were deployed from this template" and future bulk-update scenarios.

**Key points:**
- **`seed_app_templates` management command** — Uses `update_or_create` keyed on `slug` so re-running is safe. Only creates template definitions, never deploys instances.
- **Entry points** — "From Template" links added to both workspace detail page and dashboard empty state, both using HTMX for SPA navigation.
- **Migration fix** — Hit `table already exists` error from a partial earlier migration attempt. Fixed by faking migration 0033 and deleting the duplicate 0034.
- **OpenClaw template** — First template: container port 18789, 1 vCPU / 2GB RAM, `fargate_web` stack profile, four runtime variables (OPENAI_API_KEY as secret, MODEL/MAX_TOKENS/SYSTEM_PROMPT as config).

## 2026-04-07 16:22 - [Integrations] Fix GitHub App multi-org integration: OAuth picker instead of direct install redirect

**Conversation:** [2026-04-07-1622-c1c540de.md](conversations/2026-04-07-1622-c1c540de.md)

Diagnosed and fixed a bug where connecting GitHub to a second DOH organization would silently fail. The root cause was that `github_connect` always redirected to GitHub's static installation URL (`/apps/devops-hero-app/installations/new`). GitHub's install flow only redirects back to our Setup URL when creating a **new** installation. If the GitHub App is already installed on the user's GitHub org (from the first DOH org's connection), GitHub shows a "Configure" page instead — no redirect back, so the second DOH org can never complete the connection.

The fix replaces the direct-to-install flow with a GitHub OAuth flow. Instead of sending users straight to the install page, we now:
1. Redirect to GitHub's OAuth authorize endpoint to get a user access token
2. Use `GET /user/installations` (scoped to the authenticated user — no cross-tenant leakage) to discover which GitHub orgs already have the app installed
3. Show a picker page where the user selects which GitHub installation to connect to their current DOH org
4. Also offer an "Install on a new GitHub organization" link for orgs that don't have the app yet

This properly handles the case where multiple DOH orgs need to connect to the same GitHub installation, and the case where a user has multiple GitHub orgs with the app installed and needs to pick the right one.

Security consideration: using `GET /app/installations` (the App JWT endpoint) would have listed **all** installations across all customers — a data leak. Using `GET /user/installations` with a user-scoped OAuth token ensures each user only sees installations they have access to on GitHub.

**Key points:**
- **OAuth state parameter** — CSRF protection via `secrets.token_urlsafe(32)` stored in session, verified on callback.
- **Backwards compatible** — `github_callback` checks for `installation_id` in query params and delegates to the setup handler, so old direct-install redirects still work.
- **`GITHUB_APP_CLIENT_ID` and `GITHUB_APP_CLIENT_SECRET`** were already in settings but unused — now they drive the OAuth flow.
- **Session-based picker state** — installations list is stored in the session between the OAuth callback redirect and the picker page render, following the existing HTMX app shell pattern (non-HTMX returns shell with `content_url`, HTMX fetches content).

## 2026-04-07 09:17 - [DevEx] Meridian demo seed: slimmer app list, dev env, branch→environment, workspaces UI

**Conversation:** [2026-04-07-0917-c2234d7f.md](conversations/2026-04-07-0917-c2234d7f.md)

Follow-up on `seed_prepare_demo` / Meridian demo data: removed Prism Scoring Engine and Ledgerline Expense Tracker (and the unused `ml-model-api` repo) so the dashboard is less crowded. Added a third Meridian environment `dev` (slug `dev`, same AWS account as production/staging, `us-east-1`) and taught `seed_prepare_demo` to place each fake deployment on the environment that matches the app’s git branch: `main` → production, `staging` → staging, `dev` → dev. This aligns the data model with how we tell the “which branch / which environment” story in the video.

**`seed_test_apps --reset` and demo users:** Deleting mock orgs failed when `seed_prepare_demo` had created `@meridiansystems.com` users whose only org was Meridian — Django blocks deleting the org while those users still point at it (`ProtectedError`), and `current_organization` is non-null (`IntegrityError` if cleared). Fix: for each user with `current_organization` set to the org being deleted, reassign to another membership’s org if any; otherwise **delete the user** (seed-only accounts).

**Workspaces list:** Annotated each workspace app with `latest_environment` (name of the environment from the most recent deployment) so the UI can show where each app is “running” without opening app detail.

**Key points:**
- **Branch→env map** — `BRANCH_TO_ENV_SLUG` in `seed_prepare_demo` keeps the rule explicit at the call site.
- **Idempotency** — Still one blueprint+deployment per app; environment slug is chosen from the app’s configured branch before the “already exists” check.

## 2026-04-07 08:46 - [DevEx] seed_prepare_demo: fake deployments + security data for video recording

**Conversation:** [2026-04-07-0846-c2234d7f.md](conversations/2026-04-07-0846-c2234d7f.md)

New `seed_prepare_demo` management command that layers demo-ready data on top of the existing `seed_test_apps` baseline for Meridian Systems. The separation is intentional: `seed_test_apps` stays generic and reusable; `seed_prepare_demo` is deliberately video-specific.

The workflow is: `seed_test_apps --reset` to get a clean baseline, then `seed_prepare_demo` to make everything look production-ready. `seed_prepare_demo --reset` cleans only its own artifacts (deployments, blueprints, permissions, users, groups, policies, hosted zones) without touching `seed_test_apps` data.

**Key points:**
- **`created_at` override via `.update()`** — Django's `auto_now_add=True` on Deployment ignores any value passed to `create()`. The only way to backdate timestamps is to call `Deployment.objects.filter(pk=...).update(created_at=...)` immediately after creation. The dashboard's "Last deployed" reads from `created_at`, not `completed_at`.
- **App.branch must be updated too** — The dashboard card shows `app.branch` (the App model field), not the blueprint/deployment `git_ref`. Setting branch only on the blueprint and deployment had no effect on the card; the App record itself must be saved.
- **Staggered deploy ages** — Three apps get `deployed_days_ago` overrides (Catalyst ETL Runner: 74d, Ingestion Service: 63d, Reconciliation Runner: 89d) to make the "Last deployed" column look realistically varied rather than all recent.
- **No service_url for workers/scheduled jobs** — Only `app_type == web` apps get a `service_url` and subdomain. Workers and scheduled jobs get empty strings.
- **`seed_test_users` made reusable** — Added `--email-domain` parameter (default: `humanityrules.io`) so the command can be called for any org. `seed_prepare_demo` calls it with `meridiansystems.com` to populate Meridian's People page.
- **Finance group clearance fixup** — `seed_test_apps` creates Finance with `clearance=restricted`. The video script says "I'm in the Finance group with internal clearance." `_prepare_security()` patches this to `clearance=internal` and Contractors to `clearance=external` after groups are created.
- **3 custom demo policies** — Created as `is_system=False` so they appear as deliberate governance rules in the Policies UI: Finance team workspace access (department=finance + clearance=internal → workspace:edit), Production deploys for internal admins only, and external contractors view-only.
- **`call_command()` for reuse** — `_prepare_security()` delegates to `seed_test_groups` and `seed_test_users` via `call_command()` rather than duplicating logic. This keeps group/user generation consistent with the existing commands.

## 2026-04-06 20:20 - [DevEx] Overhaul seed_test_apps for demo-ready Meridian Systems org

**Conversation:** [2026-04-06-2018-fa02e780.md](conversations/2026-04-06-2018-fa02e780.md)

Preparing the `seed_test_apps` management command to produce a realistic Meridian Systems organization for demo video recording. The seed data previously only created apps with generic names and an external-facing "Customer Portal" workspace — neither fit the narrative of an internal app deployment platform.

The App model had also drifted from the raw SQL INSERT used in the command (`cpu`, `memory`, `environment_variables`, `app_secrets`, `datastore_id` columns were removed in earlier migrations), so the command was broken. Replaced all raw SQL (both INSERT and DELETE) with Django ORM calls, which also fixes FK cascade issues during `--reset`.

**Key points:**
- **Datastores added** — Two Aurora datastores (PostgreSQL Serverless for ML Feature Store, MySQL Serverless for Portal Database) seeded for Meridian Systems via `Datastore.objects.create`
- **Finance group** — Created with attributes `department=finance`, `clearance=restricted`, `cost_center=CC-400` and the seeded user as a member, using `Group`, `GroupAttribute`, `GroupMembership`
- **Finance workspace** — Added with two apps: Ledgerline Expense Tracker (django-postgres-app) and Reconciliation Runner (scheduled-tasks), giving the Finance group a coherent workspace to deploy to in the demo
- **Distinctive app names** — All 12 apps renamed from generic ("ML Model API", "Job Processor") to branded codenames ("Prism Scoring Engine", "Catalyst ETL Runner", "Watchtower Alerts", etc.)
- **Customer Portal → Internal Tools** — Renamed workspace to fit the internal-app-platform story
- **Auto-created default workspace removal** — Organization `post_save` signal creates a "Default" workspace; the seed now deletes it for orgs that don't reference it, keeping Meridian's workspace list clean
- **Raw SQL eliminated** — Both the App INSERT and the `_delete_mock_orgs` DELETE replaced with ORM, fixing schema drift and FK cascade issues

## 2026-04-05 17:54 - [UI] Simplify sidebar active-state highlighting to first-segment matching

**Conversation:** [2026-04-05-1748-47685f06.md](conversations/2026-04-05-1748-47685f06.md)

The sidebar JS used `location.pathname.startsWith(link.dataset.navUrl)` to highlight the active nav item. This broke for sections where the link target is deeper than the section root (e.g., Security links to `/security/hub/` but sub-pages like `/security/people/` don't start with `/security/hub/`). A `nav_highlight_prefix` override was added during the Integrations work to paper over this, but it added a field to Python nav dicts, a `|default:` filter in the template, and per-item special casing.

Since every sidebar section maps to a unique first path segment, the fix is simpler: compare just the first segment (`location.pathname.split('/')[1] === link.dataset.navUrl.split('/')[1]`). This lets `nav_highlight_prefix` be removed entirely from both the Python context and the template.

**Key points:**
- **Root cause** — `startsWith` with a deep URL (`/security/hub/`) can't match sibling paths (`/security/people/`)
- **First-segment match** — All sidebar sections have unique first segments (`dashboard`, `workspaces`, `environments`, `security`, `integrations`, `settings`), so comparing `split('/')[1]` is sufficient
- **Removed** — `nav_highlight_prefix` from nav item dicts in `base.py`, `|default:` filter from `_sidebar_nav.html`

## 2026-04-05 17:48 - [UI] Move AWS Accounts and Git Integrations to new Integrations sidebar entry

**Conversation:** [2026-04-05-1748-47685f06.md](conversations/2026-04-05-1748-47685f06.md)

AWS Accounts and Git Integrations were tabs under Settings, mixing org-level external-service configuration with personal/billing settings. Moved them to a new top-level "Integrations" section with its own sidebar entry, URL namespace (`/integrations/`), and tabbed UI (AWS Accounts as default tab, Git Integrations as second tab).

**Key decisions:**

- **Sidebar visibility gated by org-admin** — Non-admins don't see the Integrations entry, matching the previous Settings tab behavior where those tabs were inside an `{% if user_is_org_admin %}` block.
- **No backward-compatibility redirects** — Pre-beta product, no external consumers of old URLs. Clean break: old `/settings/aws-accounts/` and `/settings/git-integrations/` routes removed entirely.
- **`nav_highlight_prefix` for sidebar active state** — The sidebar JS uses `location.pathname.startsWith(data-nav-url)` to highlight the active item. Integrations links to `/integrations/aws-accounts/` (the default tab), but sub-pages like `/integrations/git-integrations/` wouldn't match. Added an optional `nav_highlight_prefix` field to nav items; the template uses `item.nav_highlight_prefix|default:item.url` for `data-nav-url`, so the Integrations sidebar stays active across all `/integrations/*` pages.
- **Shared `require_org_admin` in `base.py`** — Previously `_require_org_admin` was a private helper in `settings.py`. With both `settings.py` and `integrations.py` needing it, moved to `base.py` as a public function to avoid cross-module private imports.

**Key points:**
- Settings now only has Personal, Organization, and Billing tabs (org admin gated for the latter two)
- New `views/integrations.py` module with 4 views: root redirect, aws_accounts, aws_accounts_add, git_integrations
- Templates under `integrations/` follow the same `extends` pattern as Settings (base tab shell + content blocks)
- GitHub OAuth callback in `views/github.py` updated to redirect to `/integrations/git-integrations/`
- Cross-references in `environments.html` ("Connect AWS Account") and `_repo_picker_modal.html` ("Connect GitHub") updated to new URL names

## 2026-04-04 11:43 - [UI] Global button cursor (pointer / not-allowed) and cleanup

**Conversation:** [2026-04-04-1143-e821ff2e.md](conversations/2026-04-04-1143-e821ff2e.md)

Interactive affordance for buttons was inconsistent: many `<button>` elements lacked Tailwind `cursor-pointer`, so the default arrow cursor appeared even on clickable controls. Fixing every instance in templates would be noisy and easy to miss.

**Approach:** Add a single `@layer base` block in `tailwindtheme_app/static_src/src/styles.css`: `button:enabled { cursor: pointer; }` and `button:disabled { cursor: not-allowed; }`, then rebuild with `uv run manage.py tailwind build`. Tailwind utilities such as `cursor-default` or `disabled:cursor-not-allowed` still override the base rule when needed (e.g. the custom dropdown trigger that intentionally uses `cursor-default`).

**Follow-up:** After the global rule existed, redundant `cursor-pointer` classes were removed from `<button>` elements across several templates (editors, modals, security hub, tags, blueprint row, repo picker, etc.). Non-`<button>` interactive nodes still need explicit cursors where the global rule does not apply: `<summary>` for `<details>`, text inputs used as combobox triggers, clickable `<div>` wrappers, and `<svg>` chevrons.

**Key points:**
- **Base-layer `button` cursors** — One place to define default pointer for enabled buttons and not-allowed for disabled, without per-component repetition.
- **Utilities override base** — Keep `cursor-default` or other `cursor-*` on elements that must not show a hand (e.g. select-like triggers).
- **Strip redundant `cursor-pointer` from buttons only** — Leave `cursor-pointer` on non-button elements that are not covered by the `button` selector.

## 2026-03-30 18:11 - [Deployment] App detail: blueprint config accordion (datastore, env vars, secrets)

**Conversation:** [2026-03-30-1811-43994b69.md](conversations/2026-03-30-1811-43994b69.md)

Runtime configuration (datastore link, environment variables, app secrets) moved from `App` to `DeploymentBlueprint`, but the app detail page still only showed source/build/container and a per-environment deployment row without that data. The deployment editor’s `_blueprint_section.html` already listed these fields for a single blueprint; the gap was read-only visibility on the app detail “Deployed to Environments” table.

**Rendering options** were compared: (A) expandable rows, (B) tabs per environment, (C) merged/diff view across environments, (D) separate sections per config type, (E) link out to a dedicated page. **Option A (accordion)** was chosen — minimal layout change, one row per environment already matches one blueprint, and comparison across envs is still possible by expanding multiple rows.

**Implementation:** `_app_blueprint_row.html` got Alpine `x-data="{ open: false }"` on the `<tr>`, a “Config” disclosure with chevron, collapsed summary pills (env var count, secret count, datastore name when set), and an expanded panel with the same presentation style as the deployment editor (env vars as `name=value`, secrets masked). `_build_deployed_environment_rows` in `views/apps.py` now `select_related("datastore")` to avoid N+1 queries.

**Clarification:** `_app_deployment_row.html` uses `mode` (`app_history`, `workspace`, `environment`); `_app_blueprint_row.html` is only included from `app_detail.html` and has no mode parameter.

**Follow-ups:** Config block is always visible — pills show “0 env vars” / “0 secrets” when empty; expanded panel shows “None” for empty lists. The summary row uses `flex items-center` so “Config” and pills align vertically; `mt-3` separates the block from the URL line above. Temporary test data on a local blueprint was used to verify the UI, then cleared.

**Key points:**
- **Option A accordion** — Per-environment blueprint config in the existing table row; no new routes or tabs.
- **`select_related("datastore")`** — Blueprint query in `_build_deployed_environment_rows` loads datastore names in one round trip.
- **Always-on summary** — Users always see counts (including zeros); empty expanded sections show italic “None”.
- **`_app_blueprint_row` vs `_app_deployment_row`** — Only the deployment row partial is multi-mode; blueprint row is app-detail specific.

## 2026-03-30 20:00 - [UI] Adjust column widths in Deployment and Environment editors

**Conversation:** [2026-03-30-1735-78f436aa.md](conversations/2026-03-30-1735-78f436aa.md)

Three layout tweaks were made to the editor pages to better use horizontal space.

In both the Deployment Editor and the Environment Editor, the two-panel split (config panel / chat panel) was 50/50. Since the chat panel benefits from more room for message text, both were changed to 40% config / 60% chat (`w-2/5` / `w-3/5`).

Within the Application and Deployment Blueprint cards, the label/value `<dl>` grid was an equal `grid-cols-2` split. Labels like "Name", "CPU", "Port" are short, while values can be long (URLs, paths, branch names). The grid was changed to 25% labels / 75% values using an inline `style="grid-template-columns: 25% 75%;"` since Tailwind's JIT arbitrary-value syntax (`grid-cols-[25%_75%]`) would require a build step.

**Key points:**
- **Editor panels: 40/60 instead of 50/50** — `w-1/2` replaced with `w-2/5` (config) and `w-3/5` (chat) in both `deployment_editor.html` and `environment_editor.html`. Chat panels benefit more from width; config cards are compact.
- **Card dl grid: 25/75 instead of 50/50** — Inline style used instead of Tailwind arbitrary value to avoid needing a CSS rebuild. Applied to both `_app_section.html` and `_blueprint_section.html`.

## 2026-03-30 19:30 - [UI] Show env vars and secrets explicitly in Deployment Blueprint card

**Conversation:** [2026-03-30-1724-68a7bff6.md](conversations/2026-03-30-1724-68a7bff6.md)

The Deployment Blueprint card in the Deployment Editor was displaying env vars and secrets as a terse "N configured" summary. The request was to show them explicitly so developers can verify the exact values being deployed.

Two options were considered: (A) inline key-value lists within the existing `<dl>` grid; (B) separate full-width sub-sections below the grid. Option B was implemented first, but after visual review it felt disconnected from the rest of the card's compact grid layout. Option A was then implemented instead, keeping everything inside the `<dl>` while putting the list in the `<dd>` column.

**Key points:**
- **Option A chosen** — Env vars and secrets remain as `<dt>`/`<dd>` rows inside the `grid-cols-2 <dl>`. The `<dd>` holds a `<ul class="space-y-0.5 text-xs font-mono">` listing each entry as `name=value`. This keeps the field visually consistent with all other scalar properties (Branch, CPU, Memory, etc.) and preserves the card's compact feel.
- **Secret values masked** — Secrets render as `key=••••••••` with a muted color (`text-gray-400`). The `app_secrets` field is a dict (key → value) while `environment_variables` is a list of `{name, value}` objects, so iteration differs: `{% for key, val in blueprint.app_secrets.items %}` vs `{% for var in blueprint.environment_variables %}`.
- **`break-all` on env var values** — Long values (e.g. base64-encoded tokens) get `break-all` to avoid overflowing the card width.
- **Option B trade-off** — Full-width sub-sections below the grid looked visually disconnected and added structural complexity. The grid imbalance (tall `<dd>` vs short `<dt>`) in Option A is acceptable given that the list items are tiny (`text-xs`) and the card is already variable-height.

## 2026-03-30 18:52 - [UI] Replace browser prompt() with proper modal for workspace creation

**Conversation:** [2026-03-30-1653-adec5b6d.md](conversations/2026-03-30-1653-adec5b6d.md)

The "New Workspace" button on the workspaces list page was using a raw `window.prompt('Workspace name')` browser dialog to collect the name, then submitting a hidden form. This was the only place in the app still using a native browser dialog — everything else uses the project's custom modal pattern. Replaced it with a proper modal that matches the rest of the UI.

**Key points:**
- **Followed the hidden-toggle modal pattern** — Used the same approach as `_repo_picker_modal.html` and `_aws_account_picker_modal.html`: a `hidden fixed inset-0 z-50` container toggled by adding/removing the `hidden` class. This was chosen over the HTMX-into-`#modal-container` pattern (used by `_confirm_modal.html`) because the form is static and doesn't need server-rendered content.
- **Modal structure** — Header with title/subtitle/close-X, form body with labeled text input (`required`, placeholder with examples), footer with Cancel/Create buttons. All styling matches existing modals: `rounded-xl shadow-xl`, same backdrop opacity, same button classes (indigo primary, ring-inset cancel).
- **UX touches** — Escape key closes the modal (keydown listener, same as repo picker). MutationObserver clears and auto-focuses the input each time the modal opens (same technique as repo picker's search field reset). The `required` attribute on the input prevents empty submissions — previously the `prompt()` path silently did nothing on empty input.
- **Simplified the trigger** — The old implementation had a `<form>` wrapping the dashed card button, with a hidden `<input name="name">` and inline JS to bridge `prompt()` → hidden input → `form.submit()`. Now it's just a plain `<button>` that opens the modal; the `<form>` with CSRF token lives inside the modal partial.

## 2026-03-30 17:46 - [UI] Add Recent Deployments to workspace detail page

**Conversation:** [2026-03-30-1646-cfc7d981.md](conversations/2026-03-30-1646-cfc7d981.md)

Added a "Recent Deployments" table to the workspace detail page, matching the pattern already used on the environment detail page. Previously the workspace detail only showed apps and datastores — you had to navigate into an individual app or environment to see deployment history. Now the workspace page gives an at-a-glance view of the latest 20 deployments across all apps in that workspace.

**Key points:**
- **New deployment row mode** — Added `mode="workspace"` to `_app_deployment_row.html`. It shows the App name (linked to app detail) and Environment name (linked to environment detail) plus git ref. This is distinct from `mode="environment"` (which shows App + Workspace) and `mode="app_history"` (which shows Environment + Account/Region). In the workspace context, the workspace is already known, so the row highlights the app-to-environment relationship instead.
- **View query** — Added a `Deployment.objects.filter(app__workspace=workspace)` query in `workspace_detail` with `select_related("app", "environment", "environment__aws_account")` to avoid N+1 queries when rendering rows. Limited to 20 most recent, same as the environment detail page.
- **Template structure** — Reused the exact same table markup (colgroup, sr-only thead, tbody border classes) from `environment_detail.html` to keep visual consistency. Empty state shows a dashed-border box with "No deployments in this workspace yet."

## 2026-03-30 17:00 - [UI] App card name typography improvements

**Conversation:** [2026-03-30-1639-a656b4d4.md](conversations/2026-03-30-1639-a656b4d4.md)

Iterative design pass on `_app_card.html` to make app names visually prominent on the workspace detail page. The name was previously `font-medium` at default size — indistinguishable in weight from the metadata rows below it.

**Key points:**
- **Option 1 (kept): size + weight** — Changed from `font-medium` to `text-lg font-semibold`. The larger size creates clear typographic hierarchy; the name becomes the dominant element in the card at a glance.
- **Option 2 (rejected): indigo color** — Tried always-on `text-indigo-400` to signal the name as a clickable title. Rejected — the color felt out of place against the neutral dark card and clashed with indigo's existing use as an interactive/hover cue throughout the UI.
- **Option 3 (kept): separator line** — Added `border-b border-gray-200 dark:border-gray-700 pb-3` to the name row div. This draws a structural boundary between the title and the metadata rows, reinforcing hierarchy without relying on color. Combined with Option 1 this was the winning combination.
- **White glow (rejected): text-shadow** — Tried `text-shadow: 0 0 12px rgba(255,255,255,0.15)` as a luminous effect on dark mode. The Tailwind arbitrary value syntax (`dark:[text-shadow:...]`) didn't produce a visible result, likely not supported in Tailwind v4's JIT scanner for that specific property. Removed.
- **Final state**: `text-lg font-semibold text-white` + separator line — clean, structurally sound, no color tricks needed.

## 2026-03-30 16:33 - [UI] Remove Recent Conversations from workspace detail

**Conversation:** [2026-03-30-1633-ab03aad1.md](conversations/2026-03-30-1633-ab03aad1.md)

The "Recent Conversations" section at the bottom of the workspace detail page was removed. The section listed the last 10 AI conversations scoped to that workspace, but the information was redundant — conversations are accessible via the main chat navigation — and it added clutter to a page whose primary purpose is workspace infrastructure (apps, datastores).

**Key points:**
- **Template cleanup** — Removed the entire `{# Recent conversations section #}` block from `workspace_detail.html`. No other template changes were needed.
- **View cleanup** — Removed the `conversations_qs` queryset, the conditional `Sum` annotation for staff cost visibility, and the `conversations` context variable from `workspace_detail` in `workspaces.py`.
- **Import cleanup** — Removed the now-unused `Conversation` model import and `Sum` ORM import from `workspaces.py`. Also removed `show_costs` since it existed solely to gate cost annotation on conversations and had no other consumers on this page.
- **No data loss** — Conversations themselves are unaffected; only the surface that displayed a subset of them was removed.

## 2026-03-30 16:20 - [UI] Detail page layout refinement: tags side-by-side, inherited tags grouping

**Conversation:** [2026-03-30-1620-b6c58fac.md](conversations/2026-03-30-1620-b6c58fac.md)

Refinement pass on Workspace, App, and Environment detail pages. The main theme was moving the tags panel from a standalone full-width block below the page content to a 50/50 side-by-side layout alongside the details card. Along the way we also cleaned up the inherited tags display in the shared `_security_tags_section.html` partial.

**Key points:**
- **50/50 details + tags grid** — All three detail pages (workspace, app, environment) now wrap the details card and the tags card in `grid grid-cols-1 sm:grid-cols-2 gap-6`. On mobile they stack; at `sm` and above they sit side by side. The old standalone tags sections below the page content were removed.
- **Details card field layout** — Workspace and App details use `grid-cols-3` for their fields (Slug/Created/Apps and App Type/Created/Created By respectively) so all three appear in one row. Environment details use `grid-cols-2` (6 fields in 3 rows) because monospace values like the VPC ID are too wide for a 3-column layout at 50% card width — the arithmetic matters: ~145px per column at `grid-cols-3` isn't enough for a 22-char mono string.
- **Deployment button moved to breadcrumb row** — Previously inside the App detail card, the Deployment button now lives at the far right of the breadcrumb/title row. This makes it a page-level action rather than a card-level one, which is the better semantic framing. Implemented by wrapping the breadcrumb include in `flex items-center justify-between` with the breadcrumb in a `flex-1` div and the button as a sibling.
- **Inherited tags visual redesign** — Removed "inherited" text from inside pills. Added two labeled sections: INHERITED (dashed-border outlined pills) appears first, DIRECT (solid indigo pills) below. The all-caps `tracking-widest` label is 10px — small enough to be secondary but clearly present. When no `inherited_tags` are passed, labels are omitted entirely and the component behaves as before.
- **Inheritance architecture insight** — The ABAC engine (`get_effective_tags` in `abac.py`) and the view layer (`build_app_detail_context` in `apps.py`) both independently implement the same workspace→app inheritance query. The duplication is currently acceptable because the two consumers need different shapes (flat tuples vs separate querysets), but if inheritance rules change both places need updating. A shared `get_inherited_tags(app)` helper would centralize the rule without forcing the view into tuple-splitting gymnastics.
- **Environment detail previously used a wide table** — The original environment details card used a 6-column `<table>` with `overflow-x-auto`. This was converted to a `dl` grid to match the other detail pages and to participate cleanly in the 50/50 layout.

## 2026-03-12 23:45 - [UI] App card status pill polling on workspace detail and status-pill audit

**Conversation:** [2026-03-12-2319-7db10a42.md](conversations/2026-03-12-2319-7db10a42.md)

The status pill in the App card on the workspace detail view was static; when a deployment was in progress (building, pushing, deploying, etc.) the pill did not refresh until the user navigated away and back. We added server-driven refresh every 10 seconds when the app's latest deployment status is in an in-progress state.

**Implementation:** A new partial `_app_card_status.html` wraps the status row (label + pill). When `app.latest_status` is one of `pending`, `building`, `pushing`, `deploying`, `starting`, `teardown_pending`, or `tearing_down`, the wrapper div gets `hx-get="{% url 'app_card_status' app_slug=app.slug %}"`, `hx-trigger="load delay:10s"`, and `hx-swap="outerHTML"`. The response is the same partial; if the status has transitioned to a terminal state, the re-rendered HTML omits the polling attributes so HTMX stops. A lightweight view `app_card_status` in `apps.py` loads the app with a subquery for latest deployment status and renders only this partial. The app card template now includes this partial instead of inlining the status row. Dashboard also uses `_app_card.html`, so dashboard app cards get the same polling behavior without further change.

**Audit of all status pill usages:** We then audited every place that includes `_status_pill.html` (10 usages across 5 entity types). App cards (workspace detail + dashboard) now use the new endpoint. App detail page already refreshes the whole `#main-content` every 10s when any deployment is in progress (`should_auto_refresh`), so deployment rows and blueprint rows there stay fresh. Deployment rows poll themselves every 3s only during teardown; build/deploy states on app detail are covered by the page-level refresh. Blueprint section (deployment editor) and environment setup section (environment editor) already poll their own section endpoints every 5s when in a transient state. Workspaces list (inline app status inside workspace cards), environment detail (environment status and deployment rows), and environments list have no polling; these are summary/navigation views where full-page or section-level refresh was deemed unnecessary for now. The new `app_card_status` endpoint is intentionally only for the app-card context (single app's latest deployment status); other contexts (specific deployment row, blueprint, environment, etc.) use or would use their own endpoints.

**Key points:**
- Polling is conditional on in-progress status and self-terminates when the server returns the partial without `hx-trigger`, matching the pattern used in `_app_deployment_row.html` and `_blueprint_section.html`.
- The same partial is used for both initial render (from workspace detail or dashboard) and for the polling response, so one template and one view serve both.
- Dashboard app cards inherit the behavior because they use `_app_card.html`, which includes `_app_card_status.html`; no dashboard-specific logic was added.
- The audit confirmed no other status-pill location should use `app_card_status`; each entity type (deployment, blueprint, environment, etc.) has or would have its own refresh strategy.

## 2026-03-12 23:15 - [Bugfix] Fix squashed left column in Deployed to Environments and Recent Deployments tables

**Conversation:** [2026-03-12-2304-6a251b9a.md](conversations/2026-03-12-2304-6a251b9a.md)

The deployment tables on the app detail page (both "Deployed to Environments" and "Recent Deployments") and the Recent Deployments table on the environment detail page had their left (environment/details) column squashed, causing text like "Region: us-east-1" to wrap mid-word. The cause was a shared layout pattern: each table used a dedicated spacer column (`<col class="w-full">` and an empty `<td class="w-full" aria-hidden="true">`) intended to push status/actions to the right. That spacer took all flexible width, so the first column was given only its minimum content width and wrapped aggressively.

The fix was to remove the spacer column entirely and give the actual content cell the flexible width instead. For **Recent Deployments** (history-only, no actions): the row partial `_app_deployment_row.html` no longer renders the spacer `<td>`, and the first `<td>` got `w-full` so the environment/details content expands. The table in `app_detail.html` and `environment_detail.html` now has three columns (content, status, updated) with `<col class="w-full">` on the first; the unused "Actions" header and fifth column were removed. For **Deployed to Environments**, the same change was applied in `_app_blueprint_row.html` (remove spacer `<td>`, add `w-full` to the first `<td>`) and in `app_detail.html` (remove the spacer from the colgroup/thead but keep the real Actions column). The content column now grows to use available space, so region and ref lines no longer wrap unnecessarily.

The issue was more noticeable during the "Deploying" state because when the deployment is in progress, the Actions cell is empty (or shows a placeholder dash). With the spacer still present, both the spacer and the empty actions cell consumed space while the content column stayed narrow. Once the deployment succeeds, the Actions column contains links (Redeploy, Permissions, Tear Down), so the layout looked less broken even though the spacer was still wrong. Removing the spacer fixes the layout in all states.

**Key points:**
- Both "Deployed to Environments" and "Recent Deployments" tables used a spacer column that grabbed flexible width; the real content cell needed `w-full` and the spacer `<td>`/`<col>` removed.
- Recent Deployments has no actions, so the table was reduced to three columns (content, status, updated); Deployed to Environments keeps four columns (content, status, updated, actions).
- The squashing was always present but more visible during Deploying because the actions column is empty then; after success, the filled actions column made the bug less obvious.

## 2026-03-12 22:47 - [Bugfix] Remove pre-environment reset flow and center reset actions

**Conversation:** [2026-03-12-2246-26813cdf.md](conversations/2026-03-12-2246-26813cdf.md)

The environment setup editor previously exposed `Reset Conversation` even before an environment draft had been saved. That created an awkward special-case endpoint, `environment_editor_reset_new`, whose only responsibility was to abandon an account-scoped conversation with no persisted environment attached. This session aligned the environment editor with the app deployment editor: reset is now a capability of an existing saved draft, not of the initial unsaved chat. Hiding the control until `environment` exists let us delete the pre-environment reset route/view entirely and keep the lifecycle simpler.

After the behavior change, the reset button still looked visually too high in the header. The cause was not an incorrect container; both editors already place the button inside the same header flex row as the breadcrumb/description block, and that row intentionally uses `items-start`. The fix was to keep the structure intact and adjust the button's own self-alignment. The final choice was `self-center` in both the environment and deployment editors so the CTA stays vertically centered relative to the header content without changing the layout contract of the rest of the editor chrome.

**Key points:**
- The environment editor now follows the same saved-only reset model as the deployment editor, which avoids exposing a reset control before there is any persisted environment draft to discard.
- Removing `environment_editor_reset_new` from the view, URLconf, and exports eliminates a dead lifecycle branch and keeps reset semantics tied to a real `Environment`.
- Tests now assert the UI contract directly: the unsaved environment editor does not render reset controls, while the saved environment editor does.
- The visual alignment issue came from `items-start` on the shared header row, not from the button being outside the proper container; `self-center` on the button solved it with the smallest possible template change in both editors.

## 2026-03-12 22:46 - [Deployment] Save environment drafts before asking for approval

**Conversation:** [2026-03-12-2247-903833ec.md](conversations/2026-03-12-2247-903833ec.md)

Adjusted the environment setup flow so draft persistence happens as soon as the agent has enough information, and only after that does it ask the user whether the saved draft looks right or needs edits. The important discovery was that the underlying implementation was already mostly draft-first: `save_environment` already creates or updates the environment immediately and the chat SSE layer already refreshes the editor when that tool runs. The confusing behavior came from the environment agent prompt and the empty-state editor copy, which still told the agent to present a "would save" preview and ask for confirmation before persisting anything.

This was corrected at the instruction layer instead of the model/tool layer. The environment prompt now tells the agent to resolve the initial values, call `save_environment` without a pre-save confirmation turn, then summarize the saved draft and ask whether to `Provision now` or `Keep editing`. The empty-state setup card was updated to match this language so the UI no longer teaches the old behavior. Tests were tightened to assert both sides of the contract: the prompt must explicitly forbid pre-save confirmation, and the editor must tell the user the draft is saved as soon as name, region, and domain are known.

**Key points:**
- The bug was in workflow guidance, not persistence code: the `save_environment` tool and `environment-created` / `environment-changed-*` refresh events already supported a draft-first flow
- The fix keeps provisioning approval separate from draft persistence, which is the safer product shape: users see a real saved environment record first, then decide whether to keep editing or proceed
- Prompt text and UI text need to agree for agent-driven flows; if either one teaches the old sequence, users experience the product as inconsistent even when the backend behavior is correct

## 2026-03-13 01:05 - [Bugfix] Clear app URL on teardown so UI no longer shows dead links

**Conversation:**

After tearing down a deployment, the app URL was still visible in the UI across multiple templates (`_app_card.html`, `_app_blueprint_row.html`, `_app_deployment_row.html`). Clicking the link led to a page that no longer existed, which was confusing. The root cause was that the teardown executor updated the deployment status to `TORN_DOWN` but never cleared the `service_url` field. The templates conditionally display the URL based on `{% if deployment.service_url %}`, so a non-empty value kept showing the link regardless of teardown status. Fix: clear `service_url` to `""` in the success path of `app_deployment_teardown_executor.run_teardown()`. This is the simplest single-point fix that covers all display locations, and is semantically correct — the URL is no longer valid once the infrastructure is destroyed.

**Key points:**
- The app card subquery already filtered by `status=SUCCEEDED`, so it was mostly safe, but the blueprint row and deployment history row had no status guard around the URL display
- Clearing the field at the source (teardown executor) is preferable to adding status checks in every template

## 2026-03-13 00:25 - [Deployment] Environment editor helper consolidation and template cleanups

**Conversation:** [2026-03-12-2230-10e72632.md](conversations/2026-03-12-2230-10e72632.md)

Applied the same style of simplification as the deployment editor (see "Deployment editor helper consolidation and small cleanups") to the environment setup editor. The view had small single-purpose helpers, redundant context building, and a section-refresh endpoint that performed conversation lookup/mutation even though the partial only needed the environment. The template had dead branches around the reset button and modal.

**View changes:**
- Replaced the previous mix of `_get_resume_conversation`, `_reactivate_conversation`, `_create_account_scoped_conversation`, and `_build_environment_section_context` with two helpers: `_create_environment_editor_conversation(request, aws_account, environment=None)` and `_get_or_create_environment_editor_conversation(request, environment)`. Create optionally sets `context_environment` when resuming an existing environment; get-or-create finds or creates and reactivates in one place.
- Inlined the messages query into `_render_environment_editor()` (single line, only used there) and removed `_build_environment_section_context` — the full editor context already had the same keys, so the extra `context.update(...)` was redundant.
- `environment_editor_environment_section` now only loads the environment and renders the setup partial with `context={"environment": environment}`. It no longer calls `_get_resume_conversation`, so HTMX refreshes of the setup card do not create or reactivate conversations.
- Introduced `_reset_and_render_fresh_editor(request, aws_account)` so both `environment_editor_reset` and `environment_editor_reset_new` share the same "abandon + create fresh + render + HX-Replace-Url" logic.

**Template changes:**
- Removed the unreachable `{% if conversation %}` / `{% else %} No conversation linked {% endif %}` around the chat panel; the editor success path always has a conversation.
- Removed the outer `{% if conversation %}` around the reset button and the reset-confirm modal block. The success path always has a conversation; the only branch that matters is `{% if environment %}` vs `{% else %}` to choose `environment_editor_reset` vs `environment_editor_reset_new` for the modal's confirm URL.

**Tests:**
- Added `test_environment_editor_section_refresh_does_not_create_conversation` to assert that GET to the setup-section endpoint returns 200 and creates zero ENVIRONMENT_SETUP conversations.
- Added `test_reset_new_abandons_pre_environment_conversation_and_creates_fresh_conversation` to cover the pre-environment reset endpoint: abandon conversation, fresh conversation, correct HX-Replace-Url, no environment in context.

**Key points:**
- One "create environment-editor conversation" and one "get or create for existing environment" helper keep the flow readable and match the deployment editor pattern.
- Section refresh should be side-effect-free when the partial only needs the environment; otherwise every HTMX poll or event-driven refresh could create or reactivate conversations.
- Dead template branches (conversation always present on success path) were removed; the reset modal still correctly switches URL by environment vs pre-environment via the existing `environment` conditional.
- Deferred for a later pass: moving `aws_account` from query string to path (`/environments/new/<aws_account_id>/`), and optionally making environment cards always link to detail with a single "Resume Setup" on the detail page (like deployment editor card simplification).

## 2026-03-13 00:10 - [Deployment] Environment editor: replace "Discard Draft" with "Reset Conversation"

**Conversation:** [2026-03-12-2222-31a8e550.md](conversations/2026-03-12-2222-31a8e550.md)

Applied the same simplification from the deployment editor (see "Simplify deployment editor entry points") to the environment setup editor. The old flow had two endpoints: a GET that fetched a confirmation modal from the server (`/environments/setup/<conversation_id>/discard-draft/confirm/`) and a POST that performed the discard (`/environments/setup/<conversation_id>/discard-draft/`). After discard, the user was redirected to the environments list page, losing the editor context.

The new flow mirrors the deployment editor: a single POST endpoint (`/environments/<environment_id>/setup/reset/`) that discards the DRAFT/ERROR environment, abandons all related conversations, creates a fresh account-scoped conversation, and re-renders the editor in place. The confirmation modal uses the `<template>` + `htmx.process()` pattern for client-side rendering (no server round-trip), reusing the shared `_confirm_modal.html` partial.

The button moved from inside `_environment_editor_setup_section.html` (the left-panel setup card) to the editor header bar in `environment_editor.html`, matching the deployment editor's layout.

An initial implementation used `conversation_id` as the URL parameter (since conversations always exist even before an environment draft is saved). This was corrected to use `environment_id` to keep the URL symmetrical with the other `/setup/` endpoints (`/environments/<uuid>/setup/`, `/environments/<uuid>/setup/section/`). However, unlike the deployment editor where the app is created very early, the environment draft might not exist for a significant part of the conversation (the agent discusses regions, domains, etc. before calling `save_environment`). Gating the button on `{% if environment %}` made it disappear in the pre-draft flow. The fix was to split into two endpoints: `/environments/<uuid>/setup/reset/` for environments that exist (discards draft + abandons conversations) and `/environments/new/reset/<uuid>/` for pre-environment conversations (just abandons the conversation). The template picks the right URL based on whether an environment exists, so the button is always visible.

**Key points:**
- The `<template>` + `htmx.process()` pattern avoids a dedicated confirm endpoint while reusing the standard modal partial — same pattern established in the deployment editor
- Reset re-renders the editor in place (with `HX-Replace-Url` pointing to `/environments/new/?aws_account=<id>`) instead of navigating to the environments list, keeping the user in the setup flow
- URL parameters should match the resource being acted on — `environment_id` for the existing-environment endpoint, `conversation_id` for the pre-environment endpoint — keeping each URL consistent with its sibling routes
- The environment editor differs from the deployment editor in needing a pre-entity reset: the app is created immediately in the deployment flow, but the environment draft is created later after agent discussion, so the button must be available before the entity exists

## 2026-03-12 23:15 - [Bugfix] Teardown executor discards blueprint so UI shows correct status

**Conversation:** (current session — extract when saved)

After tearing down a deployment (e.g. simple-dashboard in dev), the app detail UI could still show "succeeded" for that environment. The deployment record was correctly updated to `torn_down` by `app_deployment_teardown_executor.run_teardown()`, but the associated `DeploymentBlueprint` was never updated and stayed `active`.

The app detail view builds "current" environment rows via `_build_deployed_environment_rows`: it loads non-DISCARDED blueprints and, for each, finds the latest deployment whose status is in `CURRENT_LIVE_DEPLOYMENT_STATUSES` (succeeded, teardown_pending, tearing_down — torn_down is excluded). It then picks the blueprint with the most recent such deployment per environment. Because the torn-down deployment's blueprint remained active and had no "live" deployment (torn_down doesn't count), the query fell back to an older blueprint in the same environment whose deployment was still `succeeded`, so the UI showed that stale deployment's status.

**Fix:** In `app_deployment_teardown_executor.run_teardown()`, after successfully setting the deployment to `TORN_DOWN`, we now set the deployment's blueprint to `DeploymentBlueprint.Status.DISCARDED` with `status_message="Discarded after teardown"`. That blueprint is then excluded by the view's `.exclude(status=DISCARDED)`, so it no longer appears as a candidate and the UI no longer falls back to an older succeeded deployment. No one-off data cleanup was added; the user planned to reset the environment.

**Key points:**
- Teardown is tracked on `Deployment.status` (TEARDOWN_PENDING → TEARING_DOWN → TORN_DOWN); the blueprint was never updated, which caused the UI to use "current" logic that ignored torn_down deployments and picked an older blueprint.
- Discarding the blueprint on teardown is the correct ongoing behavior; cleaning up historically stale active blueprints (from past teardowns) would be a one-off migration only if not resetting data.

## 2026-03-12 22:30 - [Deployment] Deployment editor helper consolidation and small cleanups

**Conversation:** (current session — extract when saved)

Follow-up simplification of the deployment editor after the entry-point and reset changes. The view layer had accumulated several tiny single-purpose helpers that made the existing-app flow hard to follow; the template had a dead branch and redundant context; and conversation creation was duplicated in two places.

**Helper consolidation (first pass):**
- Replaced `_get_resume_conversation`, `_get_latest_app_conversation_without_blueprint`, and the scattered `_reactivate_conversation` usage with a single `_get_or_create_existing_app_editor_conversation(request, app, blueprint)` that handles both "open blueprint" and "no blueprint" in one place. The existing-app entry point now does: get app → get open blueprint → resolve conversation → render.
- Inlined `_get_editor_messages()` into `_render_deployment_editor()`; the messages query is a single line and was only used there.
- Renamed `_render_existing_app_editor` to `_render_deployment_editor` and gave it explicit `workspace`, `repository`, and optional `app`/`blueprint`/`reset_url` so it could serve both the new-app and existing-app (and reset) flows from one helper.

**Three small cleanups (second pass):**
- **Dead branch:** The right panel in `deployment_editor.html` had an `{% if conversation %}` with an else showing "No conversation linked to this session." The view always passes a conversation, so that else was unreachable. Removed the branch and always include the chat panel.
- **reset_url:** The view was passing `reset_url` in context only for the reset-confirm modal. The template already has `app` when the reset button is shown, so the modal can use `{% url 'deployment_editor_reset' app_slug=app.slug %}` directly. Dropped `reset_url` from `_render_deployment_editor()` and from all call sites.
- **Conversation creation:** Both the new-app flow and `_create_app_scoped_conversation` (used by existing-app and reset) called `agent_service.create_conversation(...)` with the same deployment-mode args. Introduced `_create_deployment_editor_conversation(request, workspace, repository, app=None)` that creates the conversation and optionally sets `context_app` when `app` is provided. New-app and existing-app/reset now both use this helper.

**Key points:**
- One "resolve conversation for existing app" helper and one "create deployment-editor conversation" helper keep the flow readable without over-splitting.
- `deployment_editor_app_section` was changed to use `_get_existing_app()` for consistency with other app-slug views; no new endpoint.
- All 35 `TestAppEndpoints` tests pass after both passes. The `_chat_panel.html` partial still has its own `{% if conversation %}` empty state, which is now unreachable from the deployment editor; could be removed in a later pass if the partial is only used there.

## 2026-03-12 22:15 - [AgentChat] Chat width 100% and tool-call title truncation

**Conversation:** [2026-03-12-2142-cffba993.md](conversations/2026-03-12-2142-cffba993.md)

Session covered two UI changes in the agent chat: consistent full-width agent content and correct handling of long tool-call titles.

**Chat response width:** Agent message width was already changed by the user to 100%. The remaining places that still used `max-w-[80%]` for agent-originated content were the streaming partials for tool start, tool result, and the interactive question (AskUserQuestion). Those three were updated to `max-w-[100%]` so tool calls and question bubbles match the full-width agent bubbles. The thinking, error, and unavailable streaming partials were left at 80% so short status messages stay visually contained.

**Tool-call title overflow:** Long tool titles (e.g. "Read: src/ai_detector/lib/.../stripe_webhook_controller") were overflowing the details summary row and clipping without ellipsis, and the duration badge (e.g. "123ms") could be pushed off. The fix was to reserve space for the timing on the right and let the title text shrink and truncate. In `_message_tool_call.html`, `_streaming_tool_start.html`, and `_streaming_tool_result.html` the summary row was changed from `justify-between` with a single flexible left div to `gap-3` with: (1) a left group with `min-w-0 flex-1` containing the icons and a new wrapper div around the tool name and optional param, and (2) a `shrink-0` duration span. The wrapper has `min-w-0 flex-1 overflow-hidden` so it can shrink. When there is a main param for the title (e.g. file path), the display name (e.g. "Read: ") is `shrink-0` and the param span has `truncate`; when there is no param, the display name span has `truncate`. Icons were given `shrink-0` so they never compress. A `title` attribute on the wrapper shows the full tool name + param on hover. No max-width percentage was added to the text; the flex layout and `truncate` ensure the text clips within the remaining space while the timing stays visible.

**Key points:**
- Agent chat width is controlled by Tailwind `max-w-[80%]` or `max-w-[100%]` on the outer message wrapper in chat partials under `devopshero_app/templates/devopshero_app/chat/`. The compiled utility lives in `tailwindtheme_app/static/css/dist/styles.css`.
- For consistent full-width agent content, update both persisted messages (`_message.html` for agent role) and all streaming partials that render agent output (streaming start, tool start/result, question); leave thinking/error/unavailable at 80% if desired.
- Tool-call header overflow is fixed by making the summary a flex row with a shrinkable middle (title + param) and a non-shrinking timing slot. Use `min-w-0` on flex children that should shrink and `truncate` on the text node that may overflow; add `title` for full text on hover.

## 2026-03-12 21:20 - [Deployment] Simplify deployment editor entry points

**Conversation:** [2026-03-12-2121-37bed511.md](conversations/2026-03-12-2121-37bed511.md)

The deployment editor had accumulated four entry points (`/deploy/new/`, `/deploy/<slug>/`, `/deploy/<slug>/new/`, `/deploy/<slug>/resume/`) that all converged to the same conversation lookup strategy (find blueprint conversation → find pre-blueprint conversation → create new). The app detail and app card had conditional "New Deployment" / "Resume Deployment" buttons backed by `populate_deployment_entrypoint()` which annotated every app in list queries with `open_blueprint_status`. The "Discard Draft" flow used a separate confirm modal endpoint plus a POST endpoint.

**Simplification decisions:**

- **Collapsed to two entry points:** `/deploy/new/<workspace_slug>/<repo_id>/` (pre-app) and `/deploy/<slug>/` (existing app). The existing-app endpoint is the single smart entry point that resumes or creates as needed. Removed `/deploy/<slug>/new/` and `/deploy/<slug>/resume/`.

- **Moved workspace/repo from query params to path:** The pre-app URL changed from `?workspace=X&repo=Y` to path parameters. These are resource identifiers, not optional filters. The `conversation` param stays as query string since it's optional session state appended after creation. Added validation that the conversation's `context_workspace` and `context_repository` match the URL path params to prevent inconsistent state.

- **Single "Deployment" button on app detail:** Replaced the conditional "New Deployment" / "Resume Deployment" button with a static "Deployment" link to `/deploy/<slug>/`. Removed `populate_deployment_entrypoint()` and `get_open_blueprint_status_subquery()` — these ran on every app list query (dashboard, workspaces) just to compute button labels.

- **Removed "Resume Deployment" from app cards:** The card already shows deployment status via the status pill. One extra click (card → detail → Deployment) is negligible friction.

- **"Reset Conversation" replaces "Discard Draft":** A single POST endpoint (`/deploy/<slug>/reset/`) closes the current conversation, discards any DRAFT/FAILED blueprint, and creates a fresh conversation. The confirmation modal reuses the existing `_confirm_modal.html` partial via a `<template>` tag — no server round-trip needed since all content is static. The `<template>` content is copied into `#modal-container` with `htmx.process()` on click.

**Key points:**
- Net removal of ~220 lines across views, templates, and tests
- Removed 5 URL endpoints, added 1 (`reset`)
- The `DEPLOYING` blueprint status is intentionally NOT discardable by reset — only DRAFT and FAILED
- The `<template>` + `htmx.process()` pattern for client-side modal rendering avoids a dedicated confirm endpoint while reusing the standard modal partial

## 2026-03-12 19:54 - [AgentChat] Fork conversation for the deployment editor

**Conversation:** [2026-03-12-1954-c668fa4d.md](conversations/2026-03-12-1954-c668fa4d.md)

Added the ability to fork a deployment editor conversation, branching from the same agent session state. The old `/chat/<id>/fork/` endpoint only worked with the legacy chat UI and was missing `context_app` and `context_deployment_blueprint` fields. The new `/deploy/fork/<conversation_id>/` endpoint copies all conversation context and redirects back into the correct deployment editor view depending on the state (new app, existing app with/without blueprint).

The main discovery during implementation was that `session_id` was only being captured from `ResultMessage`, which arrives at the very end of the full agentic loop. In the deployment editor flow, the agent's first turn can be long (repo analysis, multiple tool calls, questions to the user), and `ResultMessage` only fires after the entire multi-turn interaction completes — not after the first response. This made fork unavailable for most of the conversation's useful lifetime. The fix was to capture `session_id` from `SystemMessage` instead, which fires at stream start and always carries it. The `ResultMessage` capture was removed since it was now redundant.

A `debug` context variable was also added to `get_app_shell_context` (from `settings.DEBUG`) so templates can conditionally show debug-only UI. The fork button in the chat panel header is gated on this, keeping it invisible in production.

**Key points:**
- `session_id` should be captured from `SystemMessage` at stream start, not from `ResultMessage` at stream end — the latter blocks forking during long multi-turn agent loops.
- The `SystemMessage.data` dict always includes `session_id` in practice, despite the generic `dict[str, Any]` typing. Direct key access (`message.data["session_id"]`) is appropriate.
- For existing-app forks, the deployment editor's "find latest conversation" logic naturally picks up the fork since it's the newest. Only the new-app flow (`/deploy/new/`) needs an explicit `?conversation=` query param because it always creates a fresh conversation.
- Debug-only UI features should be gated on `settings.DEBUG` via template context, not hardcoded or unconditionally rendered.

## 2026-03-11 17:19 - [Bugfix] App detail rows now follow live redeploy state

**Conversation:** [2026-03-11-1719-a157c661.md](conversations/2026-03-11-1719-a157c661.md)

This session started from a regression in the app detail page: clicking `Redeploy` from the `Deployed to Environments` row could end in `422 Unprocessable Content` even though the action was being offered in the UI. The underlying bug was a subtle mismatch introduced by the earlier blueprint-backed rewrite of that section. The page was correctly choosing the row owner as the latest launched blueprint for each environment, but the row contents were still anchored to the last launched deployment on that blueprint rather than the latest live deployment state. As a result, after a redeploy started, the top row could keep rendering the old `succeeded` deployment and continue to show `Redeploy`, while the backend quite reasonably rejected the second click because an active deployment already existed for that app.

The correction was to split two concerns that had been conflated in `views/apps.py`. First, the app detail page still needs a stable rule for which blueprint owns a row: pick the blueprint whose latest launched deployment is the newest successful/currently-launched record for that environment. Second, once that blueprint is chosen, the row should render from the latest live deployment on that blueprint, including in-progress statuses like `pending`, `building`, `pushing`, `deploying`, and `starting`, while still ignoring failed attempts when determining the currently launched blueprint. That keeps the top list faithful to the domain model, but it also lets the user immediately see that a redeploy is in progress and removes the stale action that caused the 422 loop.

The second part of the fix was about freshness. A deployment finishing changes more than one small fragment on `app_detail`: the top environment row can change status and actions, the `Recent Deployments` table can change status text, and the primary `New Deployment`/`Resume Deployment` action can also flip depending on whether an open blueprint still exists. Because those changes are page-wide, row-level polling would leave parts of the page out of sync. The better tradeoff was page-level HTMX polling of `app_detail` itself while any deployment for the app is in an in-progress status. The initial version used a 3-second interval, but it was intentionally relaxed to 10 seconds to reduce UI churn while still refreshing soon after a deployment succeeds or fails.

Regression coverage was expanded along the same lines. The tests now distinguish between three cases that matter for this screen: a newer failed attempt on the current blueprint should not replace the current launched state; an in-progress redeploy on that same blueprint should become the visible row state immediately; and the page should only emit the auto-refresh HTMX attributes while such an in-progress deployment exists. That test shape is important because the bug only appeared when the selector and renderer answered slightly different questions.

**Key points:**
- The top app-detail row needs two selectors, not one: one to choose the current environment blueprint and another to choose the latest live deployment state to render from that blueprint.
- Failed attempts should not displace the current launched environment row, but in-progress redeploy attempts should become visible immediately so actions and status stay honest.
- App-detail refresh is a page-level concern because deployment completion changes multiple surfaces at once, not just a single status pill.
- A 10-second HTMX poll is sufficient here; it keeps the view eventually consistent without the visual churn of tighter polling.
- Regression tests are most valuable when they encode the domain distinction that caused the bug, not just the specific HTML that happened to break.

## 2026-03-11 16:56 - [Deployment] App detail deployment lists now separate current environment state from attempt history

**Conversation:** [2026-03-11-1656-2f0c5789.md](conversations/2026-03-11-1656-2f0c5789.md)

Refined the app detail page so its two deployment lists now reflect the blueprint/deployment split from the deployment blueprint spec instead of treating all deployment rows as interchangeable history. The main product decision was that the top list, `Deployed to Environments`, should represent the app's current launched state per environment, while the bottom list, `Recent Deployments`, should remain pure attempt history. That sounds small in the UI, but it required tightening the row selection semantics and cleaning up which actions belong to which surface.

The first correction was conceptual: the old top list was built by taking the app's last 20 `Deployment` rows and deduplicating by environment, which meant the section was actually "environments seen in recent attempts", not "current deployed environments". The fix was to make the top list blueprint-backed. For each environment we now choose the current launched blueprint and require a corresponding current deployment record. That keeps the row count bounded by environments, ignores unlaunched drafts, and prevents recent failed attempts from replacing the current launched row in the summary. Once that invariant was established, `_app_blueprint_row.html` no longer needed defensive fallbacks to blueprint status/time fields and was simplified to assume `current_deployment` is always present.

The second correction was about action ownership. `Recent Deployments` is now treated as history-only, so destructive lifecycle actions no longer belong there. `Tear Down` was first moved onto the current per-environment row in the app detail page, because teardown semantically acts on the currently launched footprint in an environment rather than on an arbitrary historical attempt. After that, the shared deployment-row partial was cleaned up further so the environment detail page no longer exposes teardown either. The result is a clearer split: app detail top list is the current-state management surface, while history-style deployment tables are informational.

The third part of the session was layout stabilization. Fixed per-row grid widths solved overlap but looked too rigid; separate auto-sized row grids also could not align columns consistently. The final approach was to convert the app detail and environment deployment lists to real `table-auto` tables, render row partials as `<tr>/<td>` elements, add a spacer column that absorbs free width, and give the time cell a minimum width so long `timesince` strings do not collide with the actions column. This keeps status/time/actions visually aligned within each list without hard-coding the final spacing too aggressively.

Finally, the row-template API was simplified to match the new reality. `_app_deployment_row.html` no longer carries the obsolete `app_summary` branch that used to render the app detail top list before the blueprint-row split. The template now has only explicit `environment` and `app_history` modes, making it much easier to reason about which view owns which deployment-row behavior.

**Key points:**
- `Deployed to Environments` should be sourced from current launched blueprint state, not from a recent-deployments dedupe heuristic. That keeps the UI aligned with the domain model where `DeploymentBlueprint` owns the `(app, environment)` state and `Deployment` is attempt history.
- Current-environment summary rows need a current deployment invariant. Once the backend guarantees that, the template can stop branching between blueprint and deployment fields for status/time/ref rendering.
- Lifecycle actions should live on current-state surfaces, not history surfaces. `Recent Deployments` became cleaner once it stopped pretending to be a management surface.
- For column alignment across repeated rich rows, real tables with auto layout plus a spacer column were more robust than per-row CSS grids with guessed widths.
- Removing stale template modes matters. The unused `app_summary` branch had become misleading after the top list moved to `_app_blueprint_row.html`.

## 2026-03-11 11:31 - [Deployment] Environment setup editor mirrors deployment editor with draft-first provisioning

**Conversation:** [2026-03-11-1131-384413c1.md](conversations/2026-03-11-1131-384413c1.md)

Implemented the environment provisioning editor by explicitly mirroring the app deployment editor pattern instead of extending generic chat. The core product decision was to treat environment setup as its own task-native surface: left panel for the server-owned environment artifact, right panel for one continuous agent conversation, and stable routing that keeps the user inside the setup flow until the environment is either ready or intentionally abandoned.

The most important domain change was splitting environment persistence from environment execution. Previously `provision_environment` both created the `Environment` row and queued provisioning, which meant there was no reviewable draft state and no clean way to resume one environment task. The fix was to add `Conversation.context_environment`, introduce `draft` and `discarded` environment statuses, create a new `save_environment` tool with upsert semantics, and narrow `provision_environment` so it only transitions an existing draft or failed environment into `pending`. That keeps the job worker contract simple: only `pending` environments are claimable, while `draft` remains UI-visible but inert.

Routing and identity also needed tightening. The old environment detail route looked environments up by org plus slug even though environment slugs are only unique within an AWS account, which was risky once the editor started depending on stable resume semantics. The environment editor and environment detail flows were moved to UUID-based routes, and `save_blueprint` was hardened to reject ambiguous environment slugs across AWS accounts rather than silently picking the first match. This keeps the environment setup UI safe in multi-account organizations while preserving name/slug convenience for agent-facing prompts and listings.

The UI implementation deliberately reused the same HTMX/SSE pattern as deployment editing. `save_environment` now emits `environment-created` plus `environment-changed-{id}` notifications, and the editor uses those DOM events to move from account-scoped `/environments/new/?aws_account=...` into the stable environment-scoped setup URL and to refresh the environment setup section as the artifact changes. While provisioning is in progress, the section self-polls just like the deployment blueprint panel. Success stays inside the editor and shows a prominent callout linking to the stable environment detail page; the same success-callout style was later copied into the deployment blueprint panel for the `active` state so both task editors end with the same visual pattern.

The agent guardrails were tightened as part of the same change. Environment conversations now load `context_environment` everywhere conversation context is selected or forked, environment mode uses a narrower tool allowlist instead of the shared non-permissions tool set, and the system prompt was updated to require early draft persistence plus explicit approval before provisioning. After implementation, the prompt was tightened further to require `AskUserQuestion` not just for the final "Provision now / Keep editing" approval, but also for hosted-zone selection once the available domains are known. The intent is to make environment setup feel task-native and button-driven rather than a loose free-text chat that happens to call provisioning tools.

**Key points:**
- `Environment` is now treated like a first-class setup artifact, not an implicit side effect of provisioning. The draft-first model is what makes the environment editor resumable and reviewable.
- `Conversation.context_environment` is the environment-side equivalent of `context_deployment_blueprint`: the conversation begins account-scoped, then becomes environment-scoped once the draft exists.
- UUID routes were not just cosmetic. They were required to avoid ambiguous environment identity once a resumable editor and multi-account slug collisions became part of the design.
- SSE/HTMX remains the canonical editor refresh mechanism: tools mutate backend state, `chat.py` emits domain events, and the left panel re-renders server-owned partials instead of trusting optimistic frontend state.
- Environment mode should not rely only on prompt discipline. The narrow tool allowlist and stricter `AskUserQuestion` instructions are part of the architecture, not just UX polish.

## 2026-03-11 02:30 - [Bugfix] SSE crash when save_blueprint returns error string instead of dict

**Conversation:** [2026-03-10-1915-867cb58c.md](conversations/2026-03-10-1915-867cb58c.md)

When `save_blueprint` raised a `ValueError` (e.g., "This app already has an open deployment blueprint"), the MCP error handler returned a plain text string. `_unwrap_mcp_content` couldn't parse this as JSON, so `tool_result` in `_format_sse_event` was a string. The code then did `tool_result['app_id']` on that string, causing `TypeError: string indices must be integers, not 'str'` and crashing the SSE stream.

Traced the actual failure to conversation `019cda97-fd0c` (untitled, updated 01:53:25) — not the most recent conversation. The save_blueprint tool had `status=success` but `result_type=str` in the DB, confirming the error path. The successful conversation (`019cda97-b460`, "Simple Dashboard Deployment Setup") was a red herring — its save_blueprint worked fine.

**Key points:**
- `chat.py`: Added `isinstance(tool_result, dict)` guard before accessing dict keys on tool results. All notification handlers (`update_permission_draft`, `save_app`, `save_blueprint`, `deploy_blueprint`) were vulnerable to the same crash. Added `logger.error` when non-dict results are detected.
- `mcp_tools.py`: Catch `ValueError` from `_save_blueprint` and return a structured `_mcp_response` with `{"error": str(e), "app_id": ...}` instead of letting the exception propagate as an unstructured string. This ensures the SSE formatter can still extract `app_id` and fire `blueprint-changed-{app_id}` to reload the blueprint section in the UI.
- The `@tool` decorator's error handling for `ValueError` was returning `is_error=False` (SDK recorded `status=success` in the DB for a failed tool call), so catching the error ourselves and returning structured data doesn't change error semantics for the LLM.

## 2026-03-10 19:09 - [Deployment] Add app detail page link to deployment success message

**Conversation:** [2026-03-10-1910-9bccc8d0.md](conversations/2026-03-10-1910-9bccc8d0.md)

After a successful deployment, the agent only provided the live app URL. Added an app detail page link so users can quickly navigate to the app's management page (deployments, settings, actions) from the success message.

Two changes: added `app_slug` to the `DeploymentStatus` dataclass returned by `get_deployment_status`, and updated the deployment agent system prompt to instruct the agent to render a "View app details" link (`/apps/{app_slug}/`) alongside the live app URL on SUCCEEDED status.

**Key points:**
- `get_deployment_status` previously lacked `app_slug`, which meant the agent couldn't construct the app detail URL from the tool response alone. Now included via `deployment.app.slug`.
- System prompt `<polling>` section updated: on SUCCEEDED, agent now provides both the live service URL and an app detail page link as HTML anchors with `target="_blank"`.

## 2026-03-10 22:15 - [Deployment] Blueprint section: polling while deploying, fix query to show terminal states

**Conversation:** [2026-03-10-1724-a6dd5872.md](conversations/2026-03-10-1724-a6dd5872.md)

After the agent finished deploying from the deployment editor, the blueprint panel was stuck on "Deploying" (or reverted to "Pending"). Two issues found and fixed.

**Issue 1 — No frontend notification when deployment completes:** The established pattern is SSE-driven: on `tool_result` for `save_blueprint` or `deploy_blueprint`, `chat.py` emits `sse-notify` with `blueprint-changed-{app_id}`, the chat panel dispatches a DOM event, and the blueprint section refreshes. This works for the initial "Deploying" transition. But when the job worker finishes (`app_deployment_executor`), it only updates the DB — no event is emitted, so the UI never refreshes.

**Fix 1 — HTMX self-polling:** When `blueprint.status == 'deploying'`, `_blueprint_section.html` now renders a hidden div with `hx-get` to the blueprint section URL, `hx-trigger="load delay:5s"`, targeting `#blueprint-section`. Every 5s the section re-fetches; once status is `active` or `failed`, the re-rendered partial drops the polling div and it stops. The div is inside the main card (single root element) to avoid HTMX innerHTML swap issues with multiple roots. Mirrors the existing teardown polling pattern in `_app_deployment_row.html`.

**Issue 2 — Blueprint disappears after deployment succeeds:** `deployment_editor_blueprint_section` used `get_open_blueprint()`, which filters by `OPEN_BLUEPRINT_STATUSES = (draft, failed, deploying)`. Once the job worker sets the blueprint to `active`, it falls out of that filter, so the view returns `blueprint=None` and the template renders "Pending". The `get_open_blueprint` filter is correct for editor entry points (deciding resume vs new) but wrong for the section partial that must show the blueprint in any state.

**Fix 2 — Broader query in section endpoint:** `deployment_editor_blueprint_section` now queries for the latest non-discarded blueprint for the app, instead of using `get_open_blueprint`. This shows the blueprint regardless of status (draft, deploying, active, failed).

**Key points:**
- Blueprint status updates from the job worker are DB-only; HTMX self-polling (5s, only while deploying) is the simplest notification mechanism.
- `get_open_blueprint` is for routing decisions (resume vs new), not for rendering the current state. The section partial needs the latest non-discarded blueprint.
- Polling div must be inside the partial's single root element, not a sibling — multiple roots break HTMX innerHTML swap.

## 2026-03-10 21:30 - [Bugfix] AskUserQuestion tool use ID missing in pending_tool_calls

**Conversation:** [2026-03-10-1439-ffa1caa9.md](conversations/2026-03-10-1439-ffa1caa9.md)

During a deployment flow, after the user answered an AskUserQuestion ("Everything looks good. Deploy this draft now?" → "Deploy now"), the logs showed: `No call info found for tool use ID: toolu_bdrk_013i527ph8vKYvRcjk3hcYL9`. The deployment still completed; the error was cosmetic but noisy.

**Root cause:** Asymmetry in how AskUserQuestion is handled in the agent stream:

- In `_handle_tool_use_blocks`, when the SDK sends an AssistantMessage with an AskUserQuestion tool use block, we intentionally skip adding it to `ctx.pending_tool_calls` (and skip emitting a `tool_start` event) because the question is rendered via the `_can_use_tool` callback instead.
- After the user answers, the SDK sends a synthetic UserMessage containing a ToolResultBlock for that same tool use ID. `_handle_tool_results` looks up the ID in `pending_tool_calls` and fails because we never registered it.

**Fix:** (1) In `_handle_tool_use_blocks`, still register AskUserQuestion in `pending_tool_calls` (so the result can be matched) but continue to skip emitting `tool_start`. (2) In `_handle_tool_results`, after matching the result by ID, skip persistence and `tool_result` emission for AskUserQuestion — the question/answer is already handled and persisted as a CHOICE message by `_can_use_tool`.

**Key points:**
- AskUserQuestion must be in `pending_tool_calls` when the result arrives, or we log "No call info found" and drop the result (gracefully, but noisily).
- We do not persist or emit tool_result for AskUserQuestion; the CHOICE message and metadata updated in `_can_use_tool` are the source of truth.

## 2026-03-09 23:15 - [Deployment] Agent flow: save drafts early, confirm before deploy; trust LLM (no backend guard)

**Conversation:** (current session)

We changed the deployment agent so it saves the app and blueprint drafts as soon as analysis (and any clarifications) provide enough data, then confirms with the user that the saved draft looks good before calling `deploy_blueprint`. The agent was launching into deployment too fast; the fix is prompt and tool-description guidance, with no backend enforcement.

**What we did:**
- **System prompt (`system_prompt_app_deployment.md`):** Reordered the deployment flow so "Save initial drafts" (save_app + save_blueprint) happens early (step 6), before Dockerfile/PR work. Added `<draft_persistence>` (call save_app/save_blueprint as soon as you have enough info; don't wait until the last moment) and `<wait_for_deploy_confirmation>` (after saving, summarize the draft, ask "Everything looks good. Deploy this draft now?" via AskUserQuestion with "Deploy now" / "Keep editing", and do not call deploy_blueprint in the same turn as save_blueprint). Steps 11–12 are "Review the saved draft" and "Deploy after explicit approval."
- **MCP tool descriptions (`mcp_tools.py`):** save_app and save_blueprint now say to use them as soon as you have enough information to persist the draft; save_blueprint's result note says to review the draft and wait for explicit confirmation before deploy_blueprint. deploy_blueprint description says to call it only after reviewing the saved draft with the user and receiving explicit confirmation.
- **Backend guard removed:** We initially added a hard check in `deploy_blueprint`: it refused to run unless the conversation had a recent AskUserQuestion with the exact confirmation text and either a selected "Deploy now"/"Yes, deploy" or a user text reply matching a list of affirmative phrases. The user asked to remove that and trust the LLM. We removed the confirmation check, constants, and helper functions from `deploy_blueprint.py` and dropped the tests that asserted the guard (require confirmation, require reconfirmation after blueprint update). Prompt and tool wording still direct the agent to save early and confirm before deploying; behavior is guidance-only.

**Key points:**
- Agent is instructed to save app + blueprint drafts as soon as analysis gives enough data, then review the draft and ask for deployment approval before calling deploy_blueprint.
- No backend enforcement: we rely on the LLM to follow the prompt and tool descriptions rather than blocking deploy_blueprint when no confirmation message is found.
- Draft persistence and wait_for_deploy_confirmation sections in the prompt define the desired flow; deploy_blueprint has no Message/CHOICE checks.

## 2026-03-09 22:45 - [Deployment] Blueprint panel: effective values, subdomain conflict resolution, URL and CPU display

**Conversation:** (current session)

We improved the deployment editor left-panel blueprint section so branch and subdomain show actual resolved values instead of "(default)", restored the previously lost conflict-aware subdomain resolution, and added full-URL and CPU vCPU display.

**Effective values (shared helper):**
- Added `devopshero_app/services/deployment_blueprint_effective_values.py` as the single place that resolves display and runtime values for a blueprint: branch (blank → `repository.default_branch`), subdomain (blank → `app.slug` with conflict handling), and derived `url` and `cpu_display`.
- View (`_build_blueprint_section_context`), `save_blueprint`, and `deploy_blueprint` all use this helper so the panel and tool/deploy behavior stay in sync. We initially showed "Inherited from repository default branch" / "Inherited from app slug" then simplified to just the resolved value per user preference.

**Subdomain conflict resolution (restored):**
- The old `deploy_app` tool (removed in the blueprint refactor) had `_check_subdomain_conflict` and `_resolve_subdomain`: default to `app.slug`, auto-suffix to `app_slug-environment_slug` when that hostname was already in use in the same hosted zone, and exclude the same (app, environment) when replacing a deployment. That logic was ported into the shared resolver so we don't lose the solved multi-environment same-hosted-zone behavior.
- `save_blueprint` now validates subdomain (including conflict check) before creating or updating a blueprint; an explicit conflicting subdomain raises and no blueprint is persisted. `deploy_blueprint` uses the same async resolver so the created `Deployment.subdomain` matches what the panel shows.
- We removed the view-layer try/except fallback for `ValueError` (user: we're a fresh app, no stale data); resolution errors now bubble.

**Panel display:**
- **URL:** When the environment has a hosted zone, the panel shows full URL (`https://{subdomain}.{hosted_zone}`) and a separate Subdomain row (subdomain before URL). When there is no hosted zone, only Subdomain is shown.
- **CPU:** The panel shows both ECS units and vCPU equivalent (e.g. "256 units (0.25 vCPU)", "2048 units (2 vCPU)") via `cpu_display` from the helper; 1024 units = 1 vCPU.

**Key points:**
- Single source of truth for blueprint effective values: `deployment_blueprint_effective_values` (sync `resolve_*`, async `aresolve_*`) used by view, save_blueprint, and deploy_blueprint.
- Conflict-aware subdomain resolution is back: same hosted zone + conflict → auto-suffix `-{env_slug}`; same app+environment excluded when replacing; explicit conflict raises before persist.
- Blueprint section shows URL (when hosted zone set), Subdomain, and CPU with vCPU; no "(default)" placeholders.
- Tests cover fallback values, auto-suffix on conflict, same-app-same-env exclusion, explicit conflict rejection without persist, deploy-time persisted subdomain, and CPU display for 256 and 2048 units.

## 2026-03-09 21:40 - [Deployment] New vs resume deployment: explicit entrypoints and discard draft

**Conversation:** [2026-03-09-2136-753f8fed.md](conversations/2026-03-09-2136-753f8fed.md)

We implemented the product contract agreed in the deployment story: the app-detail and workspace app-card deployment buttons now share one source of truth (open blueprint or not), and "New Deployment" creates a fresh app-scoped conversation with no blueprint until the agent selects an environment, while "Resume Deployment" reopens the open deployment task (blueprint as source of truth, conversation resumed or created for that blueprint). We also added a "Discard Draft" action in the deployment editor and enforced one open blueprint per app in the backend.

**Product decisions (from conversation):**
- No parallel drafts per app: at most one open blueprint (draft, failed, or deploying) at a time.
- Open = draft, failed, or deploying. All three show "Resume"; only when there is no open blueprint do we show "New".
- Resume uses the blueprint as source of truth: find or create a conversation bound to that blueprint; reactivate if completed/abandoned.
- New creates a new conversation with `context_app` set and no `context_deployment_blueprint`; the agent creates the draft blueprint later when the user selects an environment.
- Discard is only in the deployment editor (not on app detail), only for draft and failed (not deploying), and returns the user to app detail with "New Deployment" available.
- App detail and workspace app card use the same logic so both resume buttons behave identically.

**Implementation:**
- **Shared open-blueprint logic:** In `views/apps.py`, added `OPEN_BLUEPRINT_STATUSES`, `get_open_blueprint(app)`, `get_open_blueprint_status_subquery()`, and `populate_deployment_entrypoint(app, open_blueprint_status)` so app detail and workspace/dashboard app lists get a single computed `has_open_deployment_task`, `open_blueprint_status`, `deployment_primary_action_label`, and `deployment_primary_action_url`. Replaced the previous "latest blueprint by created_at" and "draft-only" rules with "latest open blueprint" (draft/failed/deploying).
- **Explicit routes:** Added `deployment_editor_app_new` (`/deploy/<app_slug>/new/`) and `deployment_editor_resume` (`/deploy/<app_slug>/resume/`). App detail and app card buttons point to these URLs based on the shared entrypoint fields. The existing `deployment_editor` (`/deploy/<app_slug>/`) remains as a backward-compatible entrypoint: it resumes if there is an open blueprint, otherwise uses or creates a no-blueprint conversation (e.g. after new-app flow redirects to app-scoped URL).
- **Resume flow:** Resume loads the open blueprint, then finds the current user's latest conversation for that blueprint (or creates one and binds it). Completed/abandoned conversations are reactivated so the user can continue in the same thread.
- **New flow:** New always creates a fresh app-scoped conversation with no blueprint. If the user had hit "New" while an open blueprint existed, we redirect to the resume URL and push that in history so the URL matches the actual state.
- **Discard draft:** New POST endpoint `deployment_editor_discard_draft` marks the open draft/failed blueprint as discarded and abandons conversations tied to it, then returns app detail HTML with `HX-Push-Url` so the client shows app detail with "New Deployment". Discard button lives in the blueprint section header in the editor (draft or failed only).
- **Backend guard:** `save_blueprint` now checks for an existing open blueprint for the app before creating a new one; if one exists, it raises a clear error so the agent cannot create a second open draft (enforces "no parallel drafts" at creation time).
- **Templates:** App detail and `_app_card` use `app.deployment_primary_action_label` and `app.deployment_primary_action_url`. App card shows open status (draft/failed/deploying) with appropriate badge styling. Workspace list view was updated to use `open_blueprint_status` for app status display where relevant.
- **Tests:** Extended app and workspace ABAC view tests for new/resume/discard entrypoints, for "New Deployment" when no open blueprint, for "Resume Deployment" for both draft and failed blueprints, and for discard updating blueprint and conversation status.

**Key points:**
- One source of truth for "resume vs new" on both app detail and workspace app card, driven by open blueprint (draft/failed/deploying).
- Explicit `/deploy/<app>/new/` and `/deploy/<app>/resume/` routes keep semantics clear; legacy `/deploy/<app>/` still works and chooses resume or no-blueprint flow by state.
- Blueprint is the source of truth for resume; conversation is (re)bound to that blueprint and reactivated if needed.
- Discard draft only in editor, only for draft/failed; server-side guard in `save_blueprint` prevents parallel open blueprints per app.
- By design: if the user starts "New Deployment" and leaves before the agent creates the first blueprint, the app still shows "New Deployment" (no open blueprint yet); we did not add a separate "resume no-blueprint conversation" signal.

## 2026-03-09 14:19 - [UI] Resume draft deployments from workspace app cards

**Conversation:** [2026-03-09-1420-753f8fed.md](conversations/2026-03-09-1420-753f8fed.md)

We closed the gap in the deployment story for `DeploymentBlueprint(status='draft')`: users could already be returned to the deployment editor at `/deploy/<app_slug>/`, but there was no explicit UI entrypoint that surfaced an unfinished draft after they left the editor. The specification already said unfinished apps should remain in the normal workspace Apps list with a clear resume action, so the right fix was not a new Blueprints section or a special sidebar destination. Instead, the existing app surfaces needed to become blueprint-aware.

The main product decision was to use `Resume Deployment` rather than `Resume Setup`. "Setup" was not an existing product term in this flow and diluted the vocabulary we had just normalized around the dedicated deployment editor. `Resume Deployment` keeps the CTA aligned with `New Deployment`, makes it clear that the user is re-entering the same deployment task, and avoids exposing `DeploymentBlueprint` as UI jargon.

The implementation made app summaries aware of the latest blueprint state in addition to deployment history. Before this change, the workspace app card status came only from `Deployment`, which meant a draft blueprint with no successful deployment still looked like a generic never-deployed app. We annotated apps with `latest_blueprint_status`, and when that value is `draft` we now show a blue `Draft` badge plus a `Resume Deployment` action that links to the deployment editor. The app detail page's top-right action was updated with the same state-aware label so the app page and workspace page agree about what the next step is.

There were a few important UX refinements after the initial implementation. Putting the resume action in a bottom footer row made the card feel visually lopsided, so we moved the CTA into the top-right slot previously used by the app-type pill and moved app type into the card body as a labeled `Type` row. We also changed the card hover behavior so hovering the `Resume Deployment` button does not light up the entire card; only hovering the main card link should trigger the card highlight. Finally, the workspace-card action intentionally uses a quieter indigo secondary treatment than the primary app-detail button: same color family to signal the same action, but reduced emphasis because it lives inside a dense summary card.

Permission boundaries also mattered here. We show the draft status to readers, but the resume CTA is only rendered for users with `workspace:edit`, so viewers are not invited into a flow that would immediately 403. Added ABAC view tests for both app detail and workspace detail to cover the draft-blueprint state and the `Resume Deployment` visibility rules.

**Key points:**
- Draft blueprints now surface in the normal workspace Apps list instead of requiring a separate Blueprints destination.
- `Resume Deployment` replaced `Resume Setup` to stay aligned with the deployment editor vocabulary and avoid introducing a new term.
- App summaries now look at blueprint state as well as deployment state, fixing the invisibility of draft-first-deploy apps.
- The workspace-card CTA is intentionally smaller and quieter than the app-detail primary button, but stays in the same indigo action family.
- Hover behavior was refined so the card highlights only when hovering the main card target, not when hovering the nested resume button.

## 2026-03-09 13:54 - [UI] Rename "deployment workspace" to "deployment editor"

**Conversation:** (current session)

Renamed the two-panel deploy UI from "deployment workspace" to "deployment editor" across the entire codebase. The motivation was that `Workspace` already has a concrete domain meaning in DOH (a `Workspace` model with its own pages and permissions), so "deployment workspace" was ambiguous — it sounded like either a subtype of `Workspace` or a nested area inside one. The correct conceptual framing is that this page is an *editor* for a specific artifact pair (`App` + `DeploymentBlueprint`), which mirrors the existing "Permissions Editor" pattern precisely: artifact panel on the left, conversation panel on the right.

The rename touched every layer consistently:

- Renamed `devopshero_app/views/deployment_workspace.py` → `deployment_editor.py`, with all four view functions updated (`deployment_editor`, `deployment_editor_new`, `deployment_editor_app_section`, `deployment_editor_blueprint_section`).
- Renamed `devopshero_app/templates/devopshero_app/deploy/deployment_workspace.html` → `deployment_editor.html`. Also renamed the JS helper function inside from `reloadWorkspace` to `reloadEditor`.
- Updated `devopshero_app/urls.py` — all four URL names now use `deployment_editor*`.
- Updated `devopshero_app/views/__init__.py` — import and `__all__` both updated.
- Updated all template call sites: `app_detail.html` (New Deployment button) and `workspaces/_repo_picker_modal.html` (repo picker link).
- Updated `docs/app_deployment_blueprint_spec.md` — all prose references updated to "deployment editor". Left historical mentions in `docs/journal.md` and `docs/conversations/` intact.

The URL path (`/deploy/...`) was intentionally left unchanged — the rename is a naming/concept fix, not a routing change.

**Key points:**
- "Deployment editor" mirrors "Permissions Editor" cleanly — both are task-native two-panel surfaces for authoring a specific draft artifact with AI assistance.
- `Workspace` being a domain entity made "deployment workspace" a loaded term; "editor" is neutral and describes the function.
- The `__all__` list in `views/__init__.py` was missing the old `deployment_workspace*` names too; added the new `deployment_editor*` names while fixing that.
- Django `manage.py check` passed after the rename with no issues.

## 2026-03-09 20:15 - [UI] Deployment workspace breadcrumb and subtitle

**Conversation:** (current session)

After the blueprint refactor, the deployment workspace breadcrumb showed "Default / Deploy — Simple Dashboard" once the agent called save_app. That read like a page title in the breadcrumb slot rather than a stable hierarchy. We aligned the breadcrumb with the app-as-identity model from app_deployment_blueprint_spec.md and reduced redundant copy.

**Breadcrumb:** Switched from "Workspace name / Deploy — App name" to "Workspaces / Workspace name / App name" (reusing the breadcrumb partial's p1/p2/current pattern). For the new-app state we use "Workspaces / Workspace name / New App". The breadcrumb now answers "where am I?" with a consistent object hierarchy; the app detail page already uses "Workspaces / Workspace / App name", so the deploy workspace matches that convention.

**Subtitle:** The line under the breadcrumb previously said "Configure and deploy **Simple Dashboard**." Once the breadcrumb already shows the app name, that repeated the same information. We changed the app-scoped subtitle to "Define the app and its deployment blueprint." so it describes the current task instead of restating the breadcrumb. The new-app state still uses "Set up and deploy a new application from **repo**." since the breadcrumb doesn't yet identify an app.

**Key points:**
- Breadcrumb: Workspaces / workspace / app name (or "New App"); no verb in the crumb, task lives in subtitle or heading
- Subtitle when app exists: "Define the app and its deployment blueprint." (stage-oriented, non-redundant)
- New-app subtitle unchanged: repo-scoped copy remains until save_app

## 2026-03-09 19:45 - [AgentChat] Chat markdown: preserve single newlines in agent messages

**Conversation:** (current session)

The deployment summary in the simple-dashboard deploy conversation rendered with "Build:", "Health check:", "Resources:", and "Database:" concatenated on the same line instead of on separate lines. Investigation showed the issue was markdown rendering, not transport or storage.

**Root cause:** The LLM sent the summary with single newlines between lines (e.g. `**Build:** Dockerfile → port 8501\n**Health check:** ...`). The stored `Message.content` in the database contained those `\n` characters verbatim — `_persist_text_message` in `agent_service.py` saves content as-is. The chat UI renders markdown with `marked.parse()` and had `marked.setOptions({ gfm: true, breaks: false })`. In standard Markdown (and with `breaks: false`), a single newline is a "soft line break" and is collapsed to a space, so those lines were merged into one paragraph.

**Fix:** In `devopshero_app/templates/devopshero_app/chat/_chat_panel.html`, set `breaks: true` so that single newlines are rendered as `<br>` and visible line breaks. The same `renderMarkdown` path is used for both stored messages (on load via `renderStoredMarkdown`) and streamed text (via `handleTextDelta` / `finalizeStreamingRender`), so existing and future agent messages with single-newline formatting (e.g. deployment summaries, lists of items) now display with one line per item.

**Key points:**
- Agent markdown is persisted verbatim; no newlines were lost in SSE or DB
- `marked` with `breaks: false` collapses single newlines to spaces per CommonMark; deployment summaries looked concatenated
- `breaks: true` preserves single newlines as `<br>`, fixing deployment summary and similar agent output without changing prompts or persistence

## 2026-03-09 12:21 - [Deployment] Blueprint refactor post-implementation cleanup

**Conversation:** [2026-03-09-1222-9e2c5d24.md](conversations/2026-03-09-1222-9e2c5d24.md)

After completing all five phases of the blueprint refactor in the prior session, this session cleaned up backward-compatibility provisions, fixed the "New Deployment" entry point, and wired up the deployment workspace's left panel to refresh when the agent creates or updates entities.

**Non-nullable blueprint FK:** The `Deployment.blueprint` FK was left nullable during the refactor for backward compatibility with legacy deployments. Since we don't need that compatibility, we removed `null=True, blank=True` and the "Nullable for legacy deployments" help_text. The teardown executor had three `if deployment.blueprint else <fallback>` guards for datastore, cpu, and memory — these were simplified to direct attribute access. Migration `0030_blueprint_fk_non_nullable` enforces the constraint at the DB level. Test setUp in `test_abac_views_apps.py` was updated to create a `DeploymentBlueprint` before creating the `Deployment`.

**New Deployment button:** The "New Deployment" button on the app detail page still pointed to `chat_new` (the old monolithic chat entry point). Updated it to link to `deployment_workspace` at `/deploy/<app_slug>/`, which is the new two-panel UI where the agent guides blueprint creation and deployment.

**SSE notify for left panel refresh:** The deployment workspace left panel wasn't updating when the agent called `save_app`. Root cause was two-fold: (1) `handleNotify` in `_chat_panel.html` only dispatched the ID-suffixed event (`doh:app-changed-<uuid>`), never the generic `doh:app-changed`, so the template's generic listener never fired; (2) for the new-app flow where `app` is None at render time, both `APP_SECTION_URL` and the ID-specific listener were absent. Fix: made `handleNotify` dispatch both the generic and ID-specific events, added the app slug to the `app-changed` SSE payload, and updated the workspace template to handle the new-app case by reloading the entire workspace at `/deploy/<slug>/`. Same pattern applied to `blueprint-changed` — when `BLUEPRINT_SECTION_URL` is null (first blueprint creation), the workspace reloads from the app-scoped URL.

**Key points:**
- `Deployment.blueprint` is now non-nullable; no fallback logic for legacy deployments without blueprints
- "New Deployment" on app detail goes to `/deploy/<app_slug>/` instead of `/chat/new/`
- `handleNotify` dispatches both generic (`doh:app-changed`) and ID-specific (`doh:app-changed-<uuid>`) events
- New-app SSE flow: server includes `slug` in the `app-changed` notify, template uses it to navigate from `/deploy/new/` to `/deploy/<slug>/`
- New-blueprint SSE flow: when `BLUEPRINT_SECTION_URL` is null, workspace reloads from `WORKSPACE_URL` so the template re-renders with the blueprint

## 2026-03-09 12:45 - [AgentChat] Simplify doh: event contract: one event per notify, declarative deployment refresh

**Conversation:** [2026-03-09-1218-95726d25.md](conversations/2026-03-09-1218-95726d25.md)

Simplified the SSE-notify → DOM event flow so the server sends a single final event name per notification and the deployment workspace uses declarative HTMX triggers instead of page-level JS for app/blueprint section refreshes.

**Server-defined event names:** Previously the server sent `{"type": "title-changed", "id": "abc123"}` and the chat panel dispatched two DOM events: `doh:title-changed` (broad) and `doh:title-changed-abc123` (targeted). Consumers were inconsistent (deployment workspace listened to broad `doh:app-changed` / `doh:blueprint-changed`; chat sidebar and permissions editor used targeted events). We changed the notify payload to `{"event": "title-changed-abc123"}` (or `app-created`, `app-changed-{id}`, `blueprint-changed-{app_id}`, etc.) so the server owns the final event name. The client (`handleNotify` in `_chat_panel.html`) now dispatches exactly one `doh:${payload.event}` per notify — no fan-out, no redundant listeners.

**Create vs update for app:** The server now distinguishes app creation from update using the `created` flag from `SaveAppResult`. It emits `app-created` (with `slug` for redirect) when the agent creates a new app, and `app-changed-{app_id}` when updating an existing app. The deployment workspace only needs one imperative listener: `doh:app-created` → `reloadWorkspace(/deploy/${slug}/)` to transition from the "new app" page to the app-scoped URL. App and blueprint section refreshes are fully declarative via `hx-trigger="doh:app-changed-{{ app.id }}"` and `doh:blueprint-changed-{{ app.id }}` with `hx-get` on the section wrappers.

**Blueprint section endpoint app-scoped:** The blueprint section refetch URL was changed from `/deploy/<blueprint_id>/blueprint-section/` to `/deploy/<app_slug>/blueprint-section/`. The view now loads the app by slug and returns the latest blueprint for that app (`.order_by("-created_at").first()`). That way the same `hx-get` URL works before and after the first blueprint exists — no need to reload the whole workspace when the agent creates a blueprint. `SaveBlueprintResult` and `DeployBlueprintResult` now include `app_id` so the server can emit `blueprint-changed-{app_id}` for targeted refresh.

**ABAC on fragment endpoints:** When adding the app/blueprint section endpoints we enforced ABAC; both use `workspace:edit` to match the parent deployment workspace view. The deploy page is an edit surface (configure and deploy), and fragment URLs are still normal HTTP endpoints, so using the same permission avoids letting view-only users read deploy state by calling the section URLs directly.

**Key points:**
- One notify payload → one DOM event name; server sends `event` with the final name (e.g. `title-changed-{id}`), client just dispatches `doh:${payload.event}`
- App create vs update: `app-created` (with slug) for redirect from /deploy/new/; `app-changed-{app_id}` for in-place section refresh
- Blueprint section is app-scoped so the HTMX trigger and URL are stable before/after first blueprint; tool results include `app_id` for the event
- Deployment workspace keeps a single imperative listener (`doh:app-created` → reload); app and blueprint sections use `hx-trigger` + `hx-get` like title/cost in chat
- Fragment endpoints use `workspace:edit` for consistency with the deploy page and to avoid exposing deploy config to view-only users

## 2026-03-09 18:00 - [Bugfix] PostHog middleware only when API key is set

**Conversation:** (current session)

Any exception (e.g. Http404 for a missing Environment) was being masked by a second exception: PostHog's `process_exception` middleware called `capture_exception()`, which triggered lazy `setup()` and raised `ValueError("API key is required")` when no API key was configured. Locally, `POSTHOG_API_KEY` is unset and `_init_posthog()` in `apps.py` correctly skips initializing the client (when `DEBUG` or no key), but the middleware was always registered, so exception handling still hit the middleware and tried to use an uninitialized PostHog.

**Fix:** Register PostHog middleware only when PostHog is configured. In `devopshero_site/settings.py`, moved `POSTHOG_API_KEY`, `POSTHOG_HOST`, and `POSTHOG_PROXY_HOST` above the `MIDDLEWARE` list, then made the middleware conditional: `if POSTHOG_API_KEY: MIDDLEWARE.append("posthog.integrations.django.PosthogContextMiddleware")`. Removed the duplicate PostHog config block that was lower in the file. Without the key, the middleware is never added, so its `process_exception` never runs and the original exception (e.g. 404) is returned as expected.

**Key points:**
- Middleware runs for every request/exception; if it depends on optional config, register it only when that config is present
- PostHog's Django integration captures exceptions in `process_exception` and assumes a client exists — lazy setup then fails when API key is missing
- Aligning middleware registration with `_init_posthog()` (key + non-DEBUG) keeps local dev free of PostHog and production behavior unchanged

## 2026-03-05 - [Bugfix] Chat: scroll to bottom after user sends a message

**Conversation:** [2026-03-05-2218-27625072.md](conversations/2026-03-05-2218-27625072.md)

When the messages list was long and the user sent a new message, the user message was appended via HTMX (`hx-target="#messages"` / `hx-swap="beforeend"`) but the scroll position of `#messages-container` was not updated, so the new message could sit below the visible area. The existing scroll logic (`scrollToBottom`, `maybeScrollToBottom`, etc.) only ran for SSE-driven updates (thinking, text deltas, tool start/result, question, complete); the initial POST response from `chat_send` was not hooked.

**Fix:** Listen for `htmx:afterSettle` on `#messages`. When the event corresponds to a POST to the chat send URL (identified via `event.detail.requestConfig.path === messageSendUrl` and `verb === 'post'`), call `enableAutoScrollAndScroll()` so we re-enable auto-scroll (in case the user had scrolled up), hide the "scroll to bottom" button, and scroll the container to the true bottom. Using `afterSettle` ensures the new message DOM is in place and laid out before we read `scrollHeight` and set `scrollTop`.

**Key points:**
- User message insert is a plain HTMX POST response; no SSE event fires for it, so we need an explicit HTMX lifecycle listener
- `htmx:afterSettle` runs after the swap and any settling (e.g. script execution), so scrollHeight is correct when we scroll
- Matching on request path (and verb) avoids reacting to other HTMX requests that might target the same element

## 2026-03-05 - [AgentChat] MainAgent refactor: callbacks as instance methods, remove dead choice_id

**Conversation:** (current session)

Refactored AskUserQuestion and agent options so that pending-question state and SDK callbacks live on `MainAgent` instead of module-level globals. Removed the legacy single-choice (`choice_id`) path that was never used by the new AskUserQuestion flow.

**Pending question state on MainAgent:** The original implementation used a module-level `_pending_questions: dict[conversation_id, PendingQuestion]` so that the sync `chat_send` view could look up and signal the answer by conversation ID. We first moved that into the agent by having `MainAgent` hold a shared mutable slot (a list) passed into `_create_agent_options` and into `__init__`, so the closure and the instance both referenced the same slot. That removed the global dict but was still a hack.

**Callbacks as instance methods:** We then refactored so that all options-building runs inside `MainAgent`: the constructor takes `conversation`, `system_prompt`, `event_queue`, etc., and calls `self._build_options()` to build `ClaudeAgentOptions` with `can_use_tool=self._can_use_tool` and `hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])]}`. The callbacks are now real instance methods (`_can_use_tool`, `_pre_tool_use_hook`) and use `self._pending_question`, `self._tool_start_times`, `self._event_queue`, `self._conversation_id` directly. No slot and no module-level `_create_agent_options`. The only async step left outside the constructor is `await client.connect(prompt=channel)`, which lives in `_connect()`; `create()` does the async prep (prompt, repo clone, fork detection), constructs the agent, then calls `await agent._connect()`.

**chat_send access path:** `chat_send` already had been updated to reach the agent via `agent_runner.get_runner(conversation.id).agent` and call `agent.submit_question_answer(answers)`. That path is unchanged; it simply now hits instance state.

**Dead choice_id cleanup:** The old template had used `choice_id` and `message` POST params for a single-choice button flow. After we removed the legacy branch from `_message_choice.html`, nothing ever sent `choice_id` anymore (AskUserQuestion sends `question_answers` JSON). We removed the `choice_id` POST read, the `elif choice_id` branch when building answers, and the `metadata={"choice_id": choice_id}` on the created user message.

**Key points:**
- Putting callbacks and their state on the agent instance avoids module-level dicts and slot hacks; the constructor can do all sync setup including building options with bound callbacks
- Keeping only `await client.connect()` out of the constructor keeps the SDK’s async connection in one place and the rest of the agent creation synchronous
- Legacy choice_id / metadata.choices code was dead after AskUserQuestion; remove it when cleaning up to avoid confusion

## 2026-03-05 - [AgentChat] Implement AskUserQuestion UI for human-in-the-loop agent decisions

**Conversation:** [2026-03-05-1213-d61c8e2f.md](conversations/2026-03-05-1213-d61c8e2f.md)

Implemented the SDK's built-in `AskUserQuestion` tool so agents can present interactive multiple-choice questions to users during conversations. This enables a human-in-the-loop pattern where the agent pauses, asks the user to choose (environment, container size, database strategy, etc.), and continues with their answer.

**Architecture — `canUseTool` callback as the control point:** The Claude Agent SDK provides a `can_use_tool` callback that intercepts tool execution requests. When the agent calls `AskUserQuestion`, our callback: (1) persists a CHOICE message to the database, (2) emits a `"question"` SSE event to the frontend, (3) blocks on an `asyncio.Event` waiting for the user's answer. The callback runs in the SDK's anyio task group, so the agent turn stays open while waiting. When the user responds, `chat_send` signals the event thread-safely via `loop.call_soon_threadsafe(event.set)`, the callback resumes, stamps selected answers on the persisted message, and returns `PermissionResultAllow` with the answers — the agent continues seamlessly.

**Multi-question batching:** The SDK batches 1–4 questions in a single `AskUserQuestion` call. Initial implementation had each option button immediately POST to `chat_send`, which meant only the last click registered. Fixed by making buttons pure selection toggles (JS state only) with a "Confirm selections" button that collects all answers into a JSON dict and submits once. The confirm button stays disabled until every question has a selection.

**Thread-safety between async callback and sync view:** The `can_use_tool` callback runs on the asyncio event loop; `chat_send` is a sync Django view running in a thread pool. The `PendingQuestion` dataclass stores a reference to the event loop (`asyncio.get_running_loop()`) so `submit_question_answer` can call `loop.call_soon_threadsafe(event.set)` safely from the sync thread.

**Persistence for page reload:** Questions are persisted as `Message.ContentType.CHOICE` with `metadata.questions` containing the full question/options structure. After the user answers, each option gets a `selected: true/false` flag stamped on the metadata. On reload, `_message_choice.html` renders the read-only answered state — selected option highlighted, others dimmed. The interactive re-wire for the rare refresh-while-pending edge case was deliberately skipped (user can type a free-text answer instead).

**SSE event flow:** Added a new `"question"` event type to `AgentEventType`. The `can_use_tool` callback puts the event directly into the runner's `event_queue` (bypassing `stream_turn`'s yield), since the callback runs in a separate task. The `_handle_assistant_message` function skips `AskUserQuestion` ToolUseBlocks to prevent rendering a tool spinner alongside the question UI.

**Key points:**
- `can_use_tool` is the official SDK mechanism for interactive tools — it keeps the agent turn alive while waiting for user input
- `asyncio.Event` + `loop.call_soon_threadsafe` bridges the async-callback / sync-view boundary safely
- Multi-question batching requires collecting all answers client-side before submitting — individual button clicks can't trigger HTTP requests
- System prompt updated (`<question_philosophy>`) to instruct agents to use `AskUserQuestion` for deployment decisions, grouping related questions into single calls with 2–4 options each

## 2026-03-04 23:13 - [Bugfix] Security tags: suggestion staleness, duplicate key rendering, and polish

**Conversation:** [2026-03-04-2314-ec8ad7db.md](conversations/2026-03-04-2314-ec8ad7db.md)

Follow-up fixes to the security tags display/edit toggle component from the same session.

**Suggestion staleness after save:** When a user added a tag with a new key and saved, the combobox suggestions didn't include the new key on the next edit. Root cause: suggestions were server-rendered `<button>` elements baked into the HTML at page load, and the save endpoint returned 204 with no HTML swap. Fix: changed save endpoints to return the rendered `_security_tags_section.html` partial with `hx-target="#security-tags" hx-swap="outerHTML"`, giving fresh suggestions, fresh tags, and a natural reset to `editing: false`. This also eliminated the HTMX-to-Alpine event bridge (`@tags-saved` / `CustomEvent`) since the entire section is replaced with server-rendered HTML.

**HTMX `hx-on` handlers can't access Alpine scope:** The previous `hx-on::after-request` handler referenced Alpine variables (`tags`, `editing`) directly, but HTMX event handlers execute in global JavaScript scope via `new Function()`, not within Alpine's reactive proxy. Variables were undefined, so the handler silently failed. Initially fixed with a custom event bridge pattern (`this.dispatchEvent(new CustomEvent('tags-saved', {bubbles:true}))` caught by Alpine's `@tags-saved`), then superseded by the outerHTML swap approach which avoids the problem entirely.

**Duplicate key rendering:** Tags with the same key but different values (e.g., `env=prod`, `env=staging`) disappeared in read-only mode. The `x-for` used `:key="item.key + item.value"` which can produce collisions via string concatenation ambiguity. Fix: changed to `:key="i"` (index-based), matching what edit mode already uses. Also added server-side deduplication (`seen` set) in all three save views to prevent truly identical tags.

**Key points:**
- `hx-on::*` handlers run in global scope, NOT Alpine's reactive scope — use outerHTML swap or event bridge to communicate between HTMX and Alpine
- Alpine `x-for` `:key` using string concatenation (`item.key + item.value`) is fragile — index-based keys are safer
- outerHTML swap on an `x-data` element is fine when you intentionally want to reset Alpine state (Alpine's MutationObserver initializes the new element)
- Save views returning rendered partials (instead of 204) keeps suggestions fresh without extra requests

## 2026-03-04 22:43 - [UI] Security tags: display/edit toggle with reusable component

**Conversation:** [2026-03-04-2244-ec8ad7db.md](conversations/2026-03-04-2244-ec8ad7db.md)

Replaced always-editable security tags with a read-only display by default and an Edit/Save/Cancel toggle for admins. Evolved through several iterations from per-row HTMX saves to a bulk Alpine array approach (matching the policy conditions editor pattern), then extracted everything into a shared `_security_tags_section.html` component used by workspace, environment, and app detail views.

The `_kv_tag_editor.html` partial was simplified to just two modes: read-only chips (server-rendered `{% for %}`) and editable rows (Alpine `x-for` over an array model). Save/Cancel buttons live in the parent component, not in the editor — same pattern as policies. Display chips are Alpine-driven (`x-for` over the `tags` array) so they stay in sync after save without needing an HTMX swap. Save endpoints return 204 No Content with `hx-swap="none"`.

**HTMX + Alpine scope bug and the event bridge pattern:** `hx-on::after-request` handlers execute in global JavaScript scope, NOT within Alpine's reactive scope. Referencing Alpine variables like `tags` or `editing` directly in `hx-on` handlers silently fails (they're undefined). The fix is an event bridge: the HTMX handler dispatches a custom DOM event (`this.dispatchEvent(new CustomEvent('tags-saved', {bubbles:true}))`), and the Alpine `x-data` element catches it with `@tags-saved="..."` where the reactive scope is available. This is the correct pattern for HTMX-to-Alpine communication.

**Other learnings:**
- `x-data='...'` must use single quotes when the value contains JSON with double quotes — otherwise the HTML attribute gets truncated at the first JSON `"`
- Alpine display chips (`x-for`) eliminate the need for HTMX swaps after save — just update the Alpine array and the UI reflects it immediately
- `{% include ... with x=y %}` (without `only`) passes all parent context plus overrides, so nested includes inherit `suggested_keys` etc. without explicit forwarding

## 2026-03-04 23:45 - [AgentChat] Measure true tool execution time via PreToolUse hook

**Conversation:** [2026-03-04-1610-70a41fb1.md](conversations/2026-03-04-1610-70a41fb1.md)

The `duration_ms` field on tool calls was inaccurate. It was captured when the `AssistantMessage` arrived (containing `ToolUseBlock`), but the actual tool execution didn't start until later — the SDK sends other `AssistantMessage` and `EventStream` messages in between, creating queuing delay. The measured time included this delay, inflating the reported duration.

The solution went through several iterations before landing on the cleanest approach: using the Claude Agent SDK's `PreToolUse` hook. This hook fires right before tool execution for **all** tools (both built-in SDK tools like Read/Write/Bash and our custom MCP tools), making it the single source of truth for timing.

**How it works:** A `PreToolUse` hook closure captures `time.time()` into a shared `tool_start_times` dict (keyed by `tool_use_id`), which lives on `MainAgent`. When `_handle_tool_results` processes the `ToolResultBlock`, it pops the start time from the same dict and computes the true duration.

**Iterations and learnings:**
- First attempt: inject `tool_duration_ms` inside each MCP tool function via `_mcp_response`, with a `_with_timing` decorator on all 19 tools. This worked for MCP tools but not built-in SDK tools.
- Tried placing the timing as a sibling field on the MCP content block (`{"type": "text", "text": "...", "tool_duration_ms": N}`), but **the SDK strips unknown fields from MCP content blocks** — only `type` and `text` survive. Had to move it inside the JSON payload instead.
- Tried an envelope approach (`{"data": ..., "tool_duration_ms": N}`) inside the JSON, which worked but required `_unwrap_mcp_content` to return a tuple and handle `_data` wrapping for list responses.
- Final approach: the `PreToolUse` hook handles all timing uniformly, so the MCP envelope, `start_time` in `_mcp_response`, and `start_time` in `pending_tool_calls` were all removed. `mcp_tools.py` is back to its original clean form.

**Key points:**
- The SDK's `PreToolUse` hook (via `HookMatcher` with no matcher = all tools) is the right place to instrument tool timing — it fires right before execution, after all queuing
- `tool_use_id` correlates `PreToolUse` with the `ToolResultBlock` — same ID in both
- The SDK strips unknown fields from MCP content blocks, so custom metadata must go inside the JSON text payload (or use hooks instead)
- `pending_tool_calls` still tracks `name` and `input` for display purposes, but no longer carries `start_time`

## 2026-03-03 21:05 - [DevEx] Rename seed script and bootstrap ABAC properly

**Conversation:** [2026-03-03-2037-a31fb710.md](conversations/2026-03-03-2037-a31fb710.md)

Renamed `seed_mock_data.py` to `seed_test_apps.py` and fixed a critical gap: the seed script was creating `OrganizationMembership(role=ADMIN)` but never calling `abac.bootstrap_organization()`. The membership role alone is not sufficient — the ABAC engine relies on `IdentityAttribute(key="org-role", value="admin")` and the 9 seed policies that `bootstrap_organization` creates. Without these, the seeded orgs had no working permission system.

The fix mirrors exactly what the real auth flows do (both WorkOS onboarding and OIDC callback): after creating the membership, call `abac.bootstrap_organization(organization=org, admin_user=user)` which creates the admin identity attribute and the 9 seed policies (3 resource types x 3 roles).

**Key points:**
- `OrganizationMembership.Role.ADMIN` is a legacy/informational field; the actual permission checks go through ABAC identity attributes (`org-role=admin`)
- `bootstrap_organization` is the canonical way to set up a new org — it's what both the WorkOS onboarding and OIDC callback use
- The rename from `seed_mock_data` to `seed_test_apps` better reflects the script's purpose

## 2026-03-03 19:58 - [UI] Hide admin-only actions from non-admin users

**Conversation:**

Two places in the UI exposed admin-only actions (connecting AWS accounts and GitHub) to all users, regardless of org role. Non-admin users seeing these links is confusing since they lack the permissions to perform the actions.

Fixed both locations to gate on the existing `user_is_org_admin` template variable (set in `views/base.py` from `abac.is_org_admin()`):

1. **Environments page empty state** (`environments/environments.html`): The "Connect AWS Account" link that appears when no environments exist and no AWS accounts are connected. Non-admins now see "No AWS account has been connected yet. Ask your organization admin to connect one."

2. **Repository picker modal** (`workspaces/_repo_picker_modal.html`): The "Connect GitHub" link shown when no repositories are connected. Non-admins now see "Ask your organization admin to connect a repository."

Note: the "New Environment" button was already correctly gated behind `user_is_org_admin` — these two empty-state links were the only ones missed.

**Key points:**
- `user_is_org_admin` is available in all templates via the base context processor, so no view changes were needed
- Follows the same pattern already used for the "New Environment" button (`{% if user_is_org_admin %}`)
- Non-admin messaging directs users to their org admin rather than showing a dead-end

## 2026-03-03 19:43 - [Onboarding] Bootstrap admin email for OIDC orgs

**Conversation:** [2026-03-03-1943-2fb605cb.md](conversations/2026-03-03-1943-2fb605cb.md)

OIDC orgs are created via `setup_oidc_org` before any user logs in, so `bootstrap_organization()` (which creates the 9 seed ABAC policies + admin identity attribute) never runs. The designated admin's first login just got `default_org_role` (viewer) like everyone else — meaning the org had no policies and no admin.

Fix: store `bootstrap_admin_email` on the `Organization` model. When an OIDC user logs in for the first time and their email matches (case-insensitive), the callback creates their membership with ADMIN role, runs `bootstrap_organization()`, and clears the field so it only fires once. Non-matching users get the existing default-role behavior.

The `setup_oidc_org` management command now prompts for the bootstrap admin email (with `--bootstrap-admin-email` flag for non-interactive use). Updated `docs/okta_oidc_setup.md` with the new field in both the prompt list and the flags example.

**Key points:**
- `bootstrap_admin_email` is an `EmailField(blank=True, default="")` — empty means no bootstrap pending, so existing orgs are unaffected
- Comparison is case-insensitive (`lower()` on both sides) since email casing varies between Okta configs
- Field is cleared after bootstrap so it's a one-shot mechanism — subsequent logins by the same email go through normal flow
- `save(update_fields=["bootstrap_admin_email"])` to avoid touching `updated_at` or racing with other org updates

## 2026-03-03 19:30 - [Integrations] Okta OIDC as second auth provider (bypass WorkOS for SSO customers)

**Conversation:** [2026-03-03-1834-1186483b.md](conversations/2026-03-03-1834-1186483b.md)

Added Okta OIDC as a second authentication provider alongside WorkOS. Motivation: WorkOS charges $125/customer for SSO, and customers who already use Okta can authenticate via standard OIDC directly, bypassing WorkOS entirely. The OIDC config (issuer URL, client ID, client secret) is stored per-organization in the database — no global settings needed.

The initial plan had several layers of indirection (email-based domain routing, per-org login endpoints that dispatched between providers, session-based callback routing) that were iteratively stripped away during implementation:

1. **Separate callback URLs instead of session dispatch** — started with a single `/auth/callback/` that checked `session["auth_provider"]` to decide between WorkOS and OIDC. Replaced with two dedicated URLs: `/auth/callback/` (WorkOS) and `/oidc/callback/` (OIDC). Cleaner because the URL itself determines the provider — no session state to manage.

2. **Removed `email_domain` field and email login form** — the plan included an email input form where we'd look up the org by domain to route to the right provider. Once we had URL-based routing, this was redundant. Removed the field, the template, and the lookup logic.

3. **Removed per-org login endpoint** — had `/auth/login/<org-slug>/` that branched on `auth_provider` (OIDC vs WorkOS). Simplified to `/oidc/login/?org=<slug>` which only handles OIDC. WorkOS login stays at `/auth/login/`. No conditional routing needed anywhere.

Final URL structure:
- `/auth/login/` — WorkOS (unchanged)
- `/auth/callback/` — WorkOS callback (unchanged)
- `/oidc/login/?org=<slug>` — starts OIDC login for an org
- `/oidc/callback/` — OIDC callback (shared by all OIDC orgs, `state` param ties back to the right org)

Created `setup_oidc_org` management command with interactive prompts for onboarding new OIDC customers. Also wrote operational docs (`docs/okta_oidc_setup.md`) covering the full Okta setup process — including two Okta gotchas we hit during testing:
- Users must be **assigned** to the app in Okta (Applications > Assignments)
- The authorization server needs an **access policy with at least one rule** — an empty policy blocks everything ("Policy evaluation failed")

**Key points:**
- OIDC config is per-org on the `Organization` model (`auth_provider`, `oidc_issuer_url`, `oidc_client_id`, `oidc_client_secret`) — no env vars or settings.py changes
- `User.oidc_sub` field links users to their OIDC identity (like `workos_user_id` for WorkOS)
- New OIDC users are auto-created in the org with default role — no onboarding flow needed since the org already exists
- Token exchange uses `httpx` (already a dependency): POST to `/v1/token`, then GET `/v1/userinfo`
- CSRF protection via `state` parameter stored in session before redirect, validated on callback

## 2026-03-02 19:49 - [UI] Parametrize _kv_tag_editor with rows mode for policy conditions

**Conversation:** [2026-03-02-1950-6eb535f9.md](conversations/2026-03-02-1950-6eb535f9.md)

The policy editor (`security_policies_detail.html`) had inline key-value condition editing code that duplicated logic from the reusable `_kv_tag_editor.html` component. The editor used Alpine-only state (arrays of `{key, value}` objects) while the kv_tag_editor used HTMX POST endpoints — fundamentally different data flows that prevented code sharing.

Parametrized `_kv_tag_editor.html` to support two modes via a new `array_model` parameter:
- **Chips mode** (default, existing): HTMX-based, shows key=value chips with inline add form. Triggered when `url_base`/`can_edit` are set.
- **Rows mode** (new): Alpine-only, one editable input row per array element with combobox dropdowns, remove button per row, and "Add" button at the bottom. Triggered when `array_model` is set (e.g., `array_model="identityConditions"`).

The policy detail page's two condition sections (identity + resource) were replaced with single `{% include %}` calls passing `array_model`, `suggested_keys`, `suggested_values`, and `add_label`.

Also added a `clear_model` parameter to `_combobox_input.html` — resets the given Alpine expression to `''` on both typing and suggestion selection. The key combobox in rows mode passes `clear_model="item.value"` so changing the key clears the value field. This was needed because chips mode had a form-level `$watch('key', () => value = '')` but rows mode had no equivalent per-row watcher.

**Key points:**
- `array_model` presence is the discriminator — no separate "mode" flag needed
- Rows mode uses `x-for="(item, i) in {{ array_model }}"` with `item.key`/`item.value` as combobox models, inheriting from parent Alpine scope
- `clear_model` is a generic combobox feature (added to `@input` and all `@click` handlers) not specific to rows mode — could be reused anywhere a paired field needs clearing
- Policy form's `submitForm()` and hidden JSON fields remain unchanged — rows mode is purely a UI refactor

## 2026-03-02 19:03 - [UI] Combobox UX improvements: focus ring fix, select-on-focus, key-value linkage

**Conversation:**

Three improvements to the `_combobox_input.html` and `_kv_tag_editor.html` components:

1. **Focus ring removal (correct fix):** The previous session's approach of adding `focus:outline-none focus:ring-0` to buttons and containers didn't work because the browser's default focus indicator uses `:focus-visible`, not `:focus`. The actual fix was appending `focus-visible:outline-none` (along with `focus:outline-none focus:ring-0`) directly to the `<input>` element inside `_combobox_input.html`. These classes are appended outside the `input_class` conditional so they apply universally. Reverted all the scattered focus-ring classes from the previous session's failed attempts on buttons, container divs, and form elements.

2. **Select-all on focus:** Added `$el.select()` to the `@focus` handler on the combobox input, so clicking into a field selects its existing content for easy replacement.

3. **Clear value when key changes:** In `_kv_tag_editor.html`, added `x-init="$watch('key', () => value = '')"` to the form. This ensures the value field is cleared whenever the key changes (typed or selected from dropdown), so the value suggestions dropdown refreshes to show options relevant to the new key. Without this, stale value text would filter out the new key's suggestions.

**Key points:**
- `focus:outline-none` targets `:focus` but browsers use `:focus-visible` for default outlines — need both
- Alpine's `$watch` doesn't fire on init, only on changes — safe for clearing dependent fields
- Focus ring classes appended outside the `{% if input_class %}` block so they apply regardless of caller

## 2026-03-02 18:56 - [UI] Policy editor button layout and focus ring cleanup

**Conversation:** [2026-03-02-1856-ce71facc.md](conversations/2026-03-02-1856-ce71facc.md)

Improved the policy editor page layout: the Delete button was sitting right next to Save Changes at the bottom, which is a UX anti-pattern (destructive action adjacent to the primary action). Moved Delete to the top-right corner of the page header and added a Cancel button next to Save that navigates back to the Policies list via HTMX.

Also cleaned up unwanted focus outline rings on the combobox dropdown buttons and the KV tag editor components — browser default focus outlines were appearing on interactive elements within these composites, creating visual noise.

**Key points:**
- Delete button now in a flex row with the page title (`justify-between`), only shown when editing (not creating)
- Cancel button uses the same HTMX pattern as the breadcrumb link (`hx-get`, `hx-target="#main-content"`, `hx-push-url`)
- Added `focus:outline-none focus:ring-0` to combobox suggestion buttons and tag editor buttons
- Added `focus-within:outline-none` to combobox container div and tag editor form to suppress composite focus rings

## 2026-03-02 18:42 - [DomainModel] Per-resource-type tag suggestions

**Conversation:** [2026-03-02-1842-8a9fd1aa.md](conversations/2026-03-02-1842-8a9fd1aa.md)

The tag editor's key/value suggestions were showing identity concepts (`org-role`, `team`, `role`) in resource tag editors (workspaces, environments, apps). These came from shared `SUGGESTED_KEYS`/`SUGGESTED_PAIRS` constants used by both `get_identity_attribute_suggestions()` and `get_resource_tag_suggestions()`.

**Two changes made:**

1. **Separated identity vs resource suggestions and made resource suggestions per-type.** Environments now suggest `stage → production/staging/development`, workspaces suggest `project`/`team`, apps have no seed suggestions yet. The DB query in `get_resource_tag_suggestions()` also filters by `resource_type` now — no schema change needed since `ResourceTag` already has that field. The policy editor passes `resource_type=None` to get the union of all types (since policies can target any resource type).

2. **Consolidated duplicated KEYS/PAIRS constants into single dicts.** Instead of separate `SUGGESTED_KEYS` and `SUGGESTED_PAIRS` sets (where keys were derivable from pairs, except for keys with no suggested values), each concept is now a single `dict[str, set[str]]` mapping keys to their value sets. A shared `_expand_suggestions()` helper derives the `(keys, pairs)` tuples the functions return. This applies to identity suggestions, resource suggestions, and system attributes.

**Key points:**
- `IDENTITY_SUGGESTIONS = {"org-role": {"admin", "member", "viewer"}, "team": set(), ...}` — keys with empty sets still appear as key suggestions
- `RESOURCE_SUGGESTIONS` is nested: `{"environment": {"stage": {"production", ...}}, ...}` — outer key is resource type
- `get_resource_tag_suggestions(org, resource_type=None)` — `None` unions all types (for the policy editor)
- No migration needed — just filtering on an existing `resource_type` column

## 2026-03-02 14:04 - [Bugfix] Deduplicate attribute chips in People list view

**Conversation:**

When both a user and their group have the same attribute (e.g., `org-role=admin`), the People list view showed two chips with identical text — one indigo (direct) and one purple (group-inherited). Without source labels in the list view, this looked like a bug.

**Fix:** Added `(key, value)` deduplication in `security_people()` view when building `member_rows`. Keeps the first source encountered per pair (system > direct > group, which is the natural append order from `get_effective_attributes()`).

**Why deduplicate in the view, not the service:** `get_effective_attributes()` is also used by the People detail view, which splits attributes into separate sections by source (system/direct/group-inherited). Deduplicating at the service level would silently hide group-inherited attributes from the detail view when a matching direct attribute exists — removing useful provenance information that helps admins decide whether a redundant direct attribute can be removed.

**Key points:**
- Policy evaluation already handles this correctly — `evaluate_policies()` collapses to a `{(k, v)}` set before matching
- The detail view intentionally shows duplicates across sections (different colors + group name labels make provenance clear)
- The list view only shows summary chips with no source labels, so duplicates are confusing there

## 2026-03-02 13:45 - [UI] Shared _kv_tag_editor.html component and _combobox_input.html Alpine.js dropdown

**Conversation:** [2026-03-02-1025-b0fb9be3.md](conversations/2026-03-02-1025-b0fb9be3.md)

Replaced all HTML5 `<datalist>` autocomplete inputs with a custom Alpine.js combobox dropdown (`_combobox_input.html`), then extracted a shared `_kv_tag_editor.html` component to eliminate duplicated chip-list + add-form markup across 5 tag/attribute templates.

**Why replace datalist:** Browser-native `<datalist>` has inconsistent rendering across browsers, zero CSS styling control, and browser autocomplete cache pollution that makes stale suggestions appear as if they're app data (see journal 2026-02-28). Alpine.js is already loaded globally so a custom dropdown is cost-free.

**Combobox architecture:** `_combobox_input.html` is a self-contained Alpine.js `x-data="{ open: false }"` scope with a filtered dropdown. The actual model variable (`key`, `value`, `cond.key`, etc.) lives in the parent Alpine scope — nested scopes inherit, so the include works in both simple tag forms and the policy editor's `x-for` loops. Suggestion values stored as `data-value` attributes (safe HTML escaping, avoids JS string quoting) — same pattern as `_permission_service_group.html`.

**Key-filtered value suggestions:** Value dropdowns don't show options until a key is selected. Suggestions are rendered as `(key, value)` pairs with `data-for-key` attributes, filtered client-side via `x-show="$el.dataset.forKey === keyModel"`. This required consolidating 4 suggestion getter functions into 2: `get_identity_attribute_suggestions(org, include_system)` and `get_resource_tag_suggestions(org)`, each returning `(sorted_keys, sorted_pairs)` tuples.

**System attribute exclusion:** `authenticated:true` is a system attribute auto-assigned to all logged-in users. Excluded from manual attribute forms (people/group) but included in policy editor conditions via `include_system=True` parameter.

**Shared component design:** `_kv_tag_editor.html` handles the common pattern: indigo chip list with remove buttons + add form with combobox inputs. Parameters: `items`, `url_base`, `hx_target`, `suggested_keys`, `suggested_values`, `can_edit`, `empty_text`, `key_width`, `value_width`. Views compute `url_base` (e.g. `/workspaces/<slug>/tags/`) to avoid Django `|add` filter issues with UUID objects.

Three simple templates (workspace tags, environment tags, group attributes) became single `{% include %}` lines. Two complex templates (app tags with inherited+direct sections, people attributes with system/direct/group-inherited/memberships) use the shared component for their editable section only.

**Key points:**
- Django `|add` filter silently returns `""` for UUID objects (str + UUID raises TypeError) — always compute URL bases in views for UUID-based entities
- Django multi-line comments must use `{% comment %}...{% endcomment %}`, not `{# ... #}` (documented in `templates/AGENTS.md`)
- `x-cloak` + `x-transition.opacity.duration.50ms` prevents dropdown flash on page load
- Chevron rotates via Alpine `:class="open && 'rotate-180'"` with CSS `transition-transform duration-200`

## 2026-03-02 09:53 - [DomainModel] Default Org-Role for New Members

**Conversation:** [2026-03-02-0953-62160c7d.md](conversations/2026-03-02-0953-62160c7d.md)

Admins can configure which org-role is assigned automatically when a new identity joins the organization. Implemented as `Organization.default_org_role` (CharField, default `"viewer"`), editable in Security > People.

**Placement decision:** Initially placed in Settings > Organization as the natural home for org-level configuration. User requested moving it to Security > People — the People tab already manages identity attributes and org-roles per member, so the default setting fits there as a banner above the member list.

**Dynamic org-roles:** Originally modeled with hardcoded choices `(admin, member, viewer)`. User clarified that org-roles are user-created ABAC identity attributes — admins can define custom roles (e.g. `contractor`, `data-scientist`). Removed `choices` from the model; the dropdown is now populated dynamically via `get_known_org_role_values(organization)` which queries `IdentityAttribute` and `GroupAttribute` for `key="org-role"` plus the seed roles `admin`, `member`, `viewer`. POST validation accepts any value in that set.

**Implementation details:**
- `assign_default_org_role(organization, user)` in `abac.py` creates the IdentityAttribute when a new member joins — hook point for future "join existing org" flow
- Dedicated `security_people_default_role` POST endpoint; form uses HTMX `hx-target="#default-role-section"` for in-place swap
- Settings > Organization reverted to org name/slug display only

**Key points:**
- Org-roles are arbitrary attribute values, not a fixed enum — UI must derive options from data
- `SEED_ORG_ROLES` in abac.py ensures admin/member/viewer always appear even in fresh orgs with no attributes yet
- Migration 0023 added the field; 0024 removed choices and increased max_length to 100 for custom role names

## 2026-02-28 23:15 - [DomainModel] Standard Org-Roles (member, viewer) and Suggestion Palette for Tag/Attribute Forms

**Conversation:** [2026-02-28-1444-b97cb274.md](conversations/2026-02-28-1444-b97cb274.md)

Previously `org-role=admin` was the only standard identity attribute. New orgs got a blank slate beyond admin, forcing manual policy setup. Added two new standard org-roles (`member`, `viewer`) with seed policies, and a suggestion palette so all tag/attribute input forms offer autocomplete.

**Org-roles and seed policies:** Discussed which roles fit the product. Considered "developer" but rejected it as too narrow for DOH's audience (data scientists, ML engineers, business staff). Settled on three roles: `admin` (existing), `member` (can view/edit workspaces, view/deploy environments, use apps), `viewer` (read-only platform access, can use apps). Each role gets 3 seed policies (one per resource type: workspace, environment, app), for 9 total system policies at bootstrap. These are normal ABAC policies with `is_system=True` — admins can edit or delete them.

**Suggestion palette:** Added pre-defined attribute keys (`org-role`, `authenticated`, `team`, `role`) and values (`admin`, `member`, `viewer`, `true`) that appear in datalist autocomplete on all key/value input forms. The palette is merged with existing org-specific keys/values from the database.

**Domain separation of suggestions:** Initially built a single `get_suggestion_keys/values` that merged everything (resource tags + identity attributes + group attributes). When testing, browser autocomplete cache made it look like resource tag keys (like `opensearch_username`) were leaking into identity attribute forms. While that turned out to be browser cache, the domain separation is correct: identity attribute forms should only suggest identity-sourced values, and resource tag forms should only suggest tag-sourced values. Split into four functions: `get_identity_attribute_suggestion_keys/values` and `get_resource_tag_suggestion_keys/values`. The policy editor template already had separate datalist IDs for identity and resource conditions — now they're properly wired to different data sources.

**Key points:**
- Org-roles are pre-populated suggestions with seed policies, not hard-coded code paths — `is_org_admin()` still only checks `org-role=admin`, the new roles work purely through standard ABAC policy evaluation
- `member` vs `viewer` distinction: member can `workspace:edit` and `environment:deploy`, viewer is read-only — neither can approve permission requests or manage tags
- Data migration (0022) adds the 6 new seed policies to existing orgs using `get_or_create` for idempotency
- Suggestion palette keys chosen to match examples already in `authorization_design_abac.md` — `department` was considered but dropped as too org-specific; `role` chosen over `job-function` for simplicity
- Browser autocomplete on `name="key"` / `name="value"` inputs can masquerade as datalist suggestions — verify the actual source before reacting

## 2026-02-28 22:10 - [DevEx] Fix staticfiles directory warning in tests

**Conversation:**

Django's `WhiteNoiseMiddleware` emits a `UserWarning: No directory at: .../staticfiles/` on the first HTTP request in the test suite. The warning appeared attached to whichever test made the first request (e.g. `test_admin_can_view_app_detail`) because Python's `warnings` module deduplicates identical warnings — the middleware is only instantiated once, on the first request, so the warning fires once and gets silenced for all subsequent tests.

Fix: created `staticfiles/.gitkeep` so the directory is tracked in git and always exists after clone. This avoids the alternative of adding `STATICFILES_DIRS` workarounds or suppressing warnings in test settings.

**Key points:**
- The warning fires during middleware instantiation on the first HTTP request, not per-test — it only appears to be test-specific due to deduplication
- Git doesn't track empty directories, so `.gitkeep` is needed to ensure `staticfiles/` exists in fresh clones
- Committed the `.gitkeep` rather than gitignoring the directory, so CI and other developers get the fix automatically

## 2026-02-28 13:27 - [DomainModel] Missing ABAC Checks in App Views and Test Suite Gaps

**Conversation:** [2026-02-28-1327-032eb58f.md](conversations/2026-02-28-1327-032eb58f.md)

Audited workspace and app views for missing ABAC permission checks. Found two unprotected endpoints in `apps.py` — `app_deployment_status` (polling) and `app_teardown_confirm` (modal fetch). Both are GET endpoints returning HTML partials that expose app/deployment information without verifying `workspace:view` on the parent workspace. Any authenticated org member could access them by guessing the app slug and deployment UUID.

Root cause for why this wasn't caught: the test suite (`test_abac_views.py`) had zero test coverage for these two endpoints. The tests were written around the main detail view and mutating actions (teardown, redeploy, tags) but the "supporting" read endpoints were overlooked. Additionally, several tested endpoints only asserted one direction — e.g. `app_tag_remove` only had an allow test with no deny test.

Added `workspace:view` checks to both endpoints, then split the monolithic `test_abac_views.py` (796 lines, 5 test classes) into four domain-specific files: `test_abac_views_workspaces.py`, `test_abac_views_apps.py`, `test_abac_views_environments.py`, `test_abac_views_security.py`. Added 8 new tests to close the gaps: allow+deny for both new endpoints, plus missing deny tests for teardown, redeploy, and tag remove. Total: 82 tests, all passing.

**Key points:**
- HTML partial endpoints (polling, modals) need the same ABAC checks as their parent views — they return the same sensitive data
- The test gap pattern was "only test the primary page + mutating endpoints" — auxiliary GET partials were missed
- Every view function should have at least one allow and one deny test to catch missing checks early
- Split large test files by domain (workspaces, apps, environments, security) rather than keeping a single monolith — easier to maintain and identify coverage gaps per area

## 2026-02-28 16:45 - [DomainModel] ABAC View Audit — Org-Admin Gates for Resource Creation and GitHub Integration

**Conversation:** [2026-02-28-1316-ec6b86a1.md](conversations/2026-02-28-1316-ec6b86a1.md)

Full audit of all view files to identify missing ABAC checks, particularly for operations that should be restricted to org-admins.

**Background:** User spotted a "New Environment" card visible to non-admin members. Investigation confirmed that the ABAC design document defines no `:create` actions for any resource type. The environment actions are `environment:view`, `environment:deploy`, `environment:approve`, and `environment:admin`. Resource creation (workspaces, environments, apps) is an org-level privilege outside ABAC policy scope — only org-admins should be able to create them.

**Template gate for New Environment card:** Changed the condition in `environments/environments.html` from `{% if aws_accounts %}` to `{% if user_is_org_admin and aws_accounts %}`. The `user_is_org_admin` context variable is already provided by `base.get_app_shell_context()`, so no backend changes were needed.

**Full view audit findings:** Reviewed all 15 view files. Every file was properly protected except `github.py`:
- `settings.py` — All admin tabs use `_require_org_admin`; personal settings open to all members
- `workspaces.py` — List filters by `workspace:view`, detail checks `workspace:view`, create checks `workspace:edit` (unscoped), tags check `workspace:admin`
- `environments.py` — List filters by `environment:view`, detail checks `environment:view`, tags check `environment:admin`
- `apps.py` — All endpoints check via parent workspace
- `security_abac.py` — Every endpoint checks `require_org_admin`
- `security_permissions_editor.py` — `apply` checks `environment:approve`; other editor endpoints scoped to user's own draft
- `chat.py` — Conversations scoped to `user=request.user`
- `dashboard.py` — Filters by `workspace:view`

**GitHub views fix:** `github_connect` and `github_callback` only had `@login_required`, meaning any org member could install or reconnect the GitHub App for the organization. Added `abac.is_org_admin()` checks to both endpoints, returning 403 for non-admins.

**Key points:**
- No `:create` ABAC actions exist by design — resource creation is always an org-admin privilege
- Template-level gates using `user_is_org_admin` are the correct approach for hiding creation UI from non-admins
- GitHub OAuth endpoints are org-level operations and must be org-admin gated, same as AWS account management in settings

## 2026-02-28 10:30 - [DomainModel] Dashboard ABAC Filtering — Platform Visibility Derived from workspace:view

**Conversation:** [2026-02-27-1819-f240c4b1.md](conversations/2026-02-27-1819-f240c4b1.md)

The dashboard view was returning all apps and datastores in the org without any ABAC filtering. Added permission checks so the dashboard only shows resources the current user is authorized to see.

The key design question was which action to filter apps by: `app:use` or `workspace:view`. The initial implementation used `app:use` (the only app-level action), but after reviewing `authorization_design_abac.md` this was wrong. The design doc defines two authorization domains: **platform access** (workspace/environment actions) governs who can see and manage resources in the DOH UI, while **app access** (`app:use`) governs who can use deployed tools through the sidecar. The dashboard is platform access, so visibility should come from `workspace:view` — defined as "See the workspace and its contents."

Both apps and datastores now filter through `workspace:view` on their parent workspace: one `filter_permitted_resources` call computes visible workspaces, then both querysets filter by `workspace__in=visible_workspaces`. This matches how the workspaces list view already uses `workspace:view`.

Updated `authorization_design_abac.md` with a new "Platform Visibility vs. App Access" subsection that makes this design decision explicit and documents the limitation: there's currently no per-app platform visibility control. A future `app:view` action could allow hiding specific apps from users who have `workspace:view` on the parent workspace, but for now the workspace boundary is the finest granularity.

**Key points:**
- `app:use` is sidecar-only; platform UI visibility is derived from `workspace:view` on the parent workspace — this wasn't explicitly documented before
- Single `visible_workspaces` queryset used for both apps and datastores, avoiding redundant ABAC evaluation
- Datastores have no ABAC resource type of their own; workspace visibility is the only access control mechanism for them
- Documented the limitation that per-app visibility control isn't possible without a future `app:view` action

## 2026-02-27 16:15 - [Bugfix] Cross-Org Conversation Context Validation in create_conversation

**Conversation:** [2026-02-27-1537-d6d5245f.md](conversations/2026-02-27-1537-d6d5245f.md)

Conversation creation accepted raw context IDs (workspace, repo, aws_account, app_permission_request) from the request without verifying they belonged to the user's organization. A user could hit `/chat/new/?workspace=<other-org-workspace-uuid>` and create a conversation whose context pointed at another org's workspace; the agent service then loaded that workspace by ID alone (no org filter) for prompts and MCP tools, leaking cross-tenant data.

Fixed by validating all context IDs at write time in `create_conversation`. A new helper `_validate_context_ownership(org, workspace_id, repo_id, aws_account_id, app_permission_request_id)` runs before creating the conversation: for each non-None ID it checks existence and org membership via `.filter(id=..., organization=org).exists()` (Workspace, Repository, AWSAccount); for AppPermissionRequest it uses `app__organization=org`. On any mismatch it raises `PermissionError` with a clear message, so the conversation is never created and downstream `aget(id=...)` calls in the agent service and MCP tools only ever see validated IDs.

**Key points:**
- Defense at write time: invalid context is rejected in `create_conversation`; no change to read paths (agent_service/mcp_tools) beyond relying on validated data
- All four context resource types validated: Workspace, Repository, AWSAccount, AppPermissionRequest (org via `app__organization`)
- Explicit `PermissionError` keeps this distinguishable from 404-style "not found" and makes cross-org attempts loud for logging/monitoring
- All 61 existing tests pass; no new tests added (validation is straightforward existence + org filter)

## 2026-02-27 15:33 - [DomainModel] ABAC Engine Cross-Org Hardening — Loud Assertions over Silent Filters

**Conversation:** [2026-02-27-1535-435457fd.md](conversations/2026-02-27-1535-435457fd.md)

Addressed two tenant-isolation gaps in the ABAC engine identified during code review: `get_effective_tags()` didn't filter ResourceTag queries by organization (so a malformed row with mismatched org FK could influence evaluation), and `filter_permitted_resources()` had a wildcard shortcut that returned the caller's queryset unchanged (so a mixed-org queryset would leak resources).

The initial fix silently filtered — `_scope_queryset_to_org` narrowed the queryset with `.filter(organization=org)`. This was changed after review to `_assert_queryset_org_scope`, which raises `ValueError` if any foreign-org resources are present. The reasoning: a mixed-org queryset reaching the engine is always a caller bug, and silently correcting it hides the defect. The engine is the right place for this assertion because it's the authorization boundary — if this check passes silently, the bug stays hidden until it manifests as a real cross-org leak.

Two assertion helpers now guard every resource-accepting entry point:
- `_assert_resource_belongs_to_org` — compares `resource.organization_id` against `organization.pk` (zero-cost FK check, no DB query). Called from `get_effective_tags`, which propagates to `evaluate_policies`, `check_action`, and `filter_permitted_resources`'s per-resource loop.
- `_assert_queryset_org_scope` — `.exclude(organization=org).count()` on the queryset. Called at the top of `filter_permitted_resources`, catching mixed-org querysets before the wildcard shortcut can return them.

Decided against user-org membership assertions on `evaluate_policies_unscoped` and `is_org_admin`: these take `(org, user)` but no resource. A membership check would require a DB query against `OrganizationMembership`, and deny-by-default already provides safety (no attributes match → no access). Cost/benefit didn't justify it.

**Key points:**
- Design decision: assertions (raise on violation) over defensive filters (silent correction) at the authorization boundary — caller bugs must be loud
- `get_effective_tags` now takes `organization` and adds it to all ResourceTag queries, closing the malformed-row data integrity gap
- `_assert_resource_belongs_to_org` uses FK IDs already on the model instance — no extra queries for workspace/app, one potential lazy load for environment's `aws_account`
- 10 new cross-org isolation tests covering: malformed tags excluded from evaluation, queryset assertion raises on mixed-org input, resource-org mismatch raises on every entry point, foreign-org policies ignored

## 2026-02-27 15:28 - [DomainModel] ABAC Test Suite and Engine Bug Fix

**Conversation:** [2026-02-27-1529-c3607d40.md](conversations/2026-02-27-1529-c3607d40.md)

Implemented the first automated test suite for the project — 58 tests covering the full ABAC policy evaluation engine (Section 1 of `docs/abac_test_plan.md`). The ABAC engine is security-critical, so it was the right place to start.

Replaced the empty `devopshero_app/tests.py` stub with a `tests/` package to accommodate the multi-section test plan. Seven test classes cover: effective attributes, effective tags, condition matching, policy evaluation, unscoped evaluation, resource filtering, org admin check, policy condition validation, and cross-org tag isolation.

Writing the tests uncovered a real bug in `filter_permitted_resources`: the wildcard optimization shortcut (lines 242-259) was short-circuiting without considering tag-scoped deny policies. If a user had a wildcard grant (e.g., org admin's seed policy granting `workspace:view` on all resources) and a tag-scoped deny existed (e.g., deny `!workspace:view` on `domain=finance`), the deny was silently ignored because the optimization only collected grants/denials from wildcard policies. The fix adds a `has_scoped_denials` check — if any matching policy has non-wildcard resource conditions with deny actions, the optimization is skipped and per-resource evaluation runs instead, where all policies are correctly evaluated together.

Also discovered and documented a behavioral inconsistency between `evaluate_policies` and `evaluate_policies_unscoped` with empty conditions: `evaluate_policies` treats `resource_conditions=[]` as vacuously true (matches everything via `_conditions_match([], set)` → `all()` on empty iterable), while `evaluate_policies_unscoped` treats it as non-matching (it checks `_is_wildcard([])` which returns `False`). The database schema prevents `None` values (JSONField has NOT NULL), so this only applies to empty lists. Tests now pin both behaviors.

**Key points:**
- First test suite in the project — structured as `devopshero_app/tests/` package for multi-section expansion
- Bug found and fixed: `filter_permitted_resources` wildcard optimization ignored tag-scoped deny policies, breaking the deny-override semantics that `evaluate_policies` correctly implemented
- Empty conditions `[]` behave differently across functions — pinned with tests rather than "fixed" since the inconsistency may be intentional (unscoped evaluation is specifically for "can user create?" checks where explicit wildcards are expected)
- All 58 tests run against SQLite in-memory in ~6 seconds — pure engine logic, no HTTP

## 2026-02-27 00:00 - [DomainModel] ABAC Refinement — Auto-tag resources with type-prefixed name on creation

**Conversation:** (active session — link TBD)

Previously only Apps got an automatic `app-name=<slug>` ResourceTag via a `post_save` signal. Workspaces and Environments had no auto-tags, meaning admins had to manually tag them before any resource-specific policy could target them. This was a gap — every resource should be policy-addressable from the moment it's created.

Added auto-tagging for Workspaces (`workspace-name=<slug>`) and Environments (`environment-name=<slug>`) using the same `post_save` signal pattern. Considered a generic `name` key across all resource types (since `resource_type` on the policy already disambiguates), but chose type-prefixed keys to stay consistent with the existing `app-name` convention and to make tags self-documenting when viewed in isolation (e.g., on the tags UI or in policy conditions).

**Key points:**
- Two new functions in `services/abac.py`: `create_default_workspace_tag()` and `create_default_environment_tag()`, both using `get_or_create` for idempotency
- Two new `post_save` signals in `models.py` for `Workspace` and `Environment`, mirroring the existing `App` signal pattern
- Environment's org is accessed via `environment.aws_account.organization` (not a direct FK)
- Existing resources don't get backfilled — would need a data migration if desired
- No new default policies for workspaces/environments (unlike apps which get a default `app:use` open-access policy) — the bootstrap seed policies already cover org-admin access with wildcard resource conditions

## 2026-02-26 21:00 - [DomainModel] ABAC Authorization System — Full Implementation

**Conversation:** [2026-02-26-2237-a9564a38.md](conversations/2026-02-26-2237-a9564a38.md)

Implemented the complete ABAC (Attribute-Based Access Control) authorization system based on the design doc in `docs/authorization_design_abac.md`. This replaces the zero-authorization state where only `@login_required` existed. The previous RBAC attempt (role bindings, group memberships) left orphan tables in SQLite that had to be dropped before migration.

**Domain model — 6 new models:**

- **IdentityAttribute** — direct key=value on a user, org-scoped. `unique_together: (org, user, key, value)` allows multiple values per key (e.g., `team=frontend` AND `team=backend`).
- **Group** / **GroupMembership** / **GroupAttribute** — groups are attribute containers. Members inherit all group attributes. This avoids duplicating attributes across many users.
- **ResourceTag** — single polymorphic model with nullable FKs to workspace/environment/app + a `CheckConstraint` ensuring exactly one FK is set (matching `resource_type`). Django 6.0 uses `condition=` instead of `check=` for `CheckConstraint` — this was caught at migration time.
- **Policy** — JSON conditions (`identity_conditions`, `resource_conditions`) with AND semantics. `[{"key": "*", "value": "*"}]` is the wildcard. Actions list supports `!` prefix for deny.

**Policy evaluation engine (`services/abac.py`):**

The core algorithm: load org policies for resource_type → compute effective attributes (system + direct + group-inherited) → compute effective tags (direct + inherited from workspace for apps) → match each policy's identity AND resource conditions → collect grants/denials → expand action hierarchy (`workspace:admin` → also `workspace:view`, `workspace:edit`) → deny-overrides (remove denied from grants).

`filter_permitted_resources()` is the list-view optimization: loads policies once, pre-filters by identity conditions, and if any matching policy has wildcard resource conditions, returns the entire queryset without per-resource evaluation. Only falls back to per-resource evaluation for non-wildcard policies.

**Bootstrapping strategy:**

New orgs (via onboarding): `abac.bootstrap_organization()` creates `org-role=admin` attribute on the admin user + 3 seed policies (workspace:admin, environment:admin, app:use for org admins). Data migration `0021` does the same for existing orgs by finding the first admin membership.

App post_save signal creates an `app-name=<slug>` ResourceTag and a wildcard-identity policy scoped to that tag, giving all authenticated users `app:use` by default. Admins can narrow this later.

**View enforcement pattern:**

Three helpers in `views/abac_helpers.py`: `check_abac(request, resource, resource_type, action)` returns `None` or `HttpResponseForbidden`, `check_abac_create()` for resource creation (evaluates with empty tags), `require_org_admin()` for settings pages. Called inline after `get_object_or_404` — early return on denial.

**Tag editors on resource pages:**

Workspace, app, and environment detail pages got tag sections with HTMX inline add/remove. App tags show both inherited workspace tags (read-only, gray badge with "inherited" label) and direct tags (editable, indigo badge). Tag add/remove views check `workspace:admin` or `environment:admin` respectively.

**Settings tabs (People, Groups, Policies):**

Added 3 new tabs to settings navigation. All require `require_org_admin`. People tab shows members with their effective attributes as colored badges (gray=system, indigo=direct, purple=group-inherited). People detail lets admins add/remove direct attributes and group memberships. Groups tab has CRUD for groups with attribute and member management. Policies tab shows policy list with IF/AND/THEN summary and an Alpine.js-powered editor with dynamic condition lists, resource-type-filtered action checkboxes, and deny toggles.

**Key points:**
- Django 6.0 `CheckConstraint` uses `condition=` not `check=` — the old parameter name raises `TypeError`
- Leftover RBAC tables from a previous design attempt (`devopshero_app_group`, `devopshero_app_approlebinding`, etc.) had to be manually dropped from SQLite before the migration could run
- Action hierarchy expansion happens after grant collection but before deny removal — so denying `workspace:view` blocks view even if `workspace:admin` is granted (deny-overrides)
- `filter_permitted_resources()` short-circuits on wildcard-resource policies to avoid N+1 tag lookups on list views
- Policy editor form uses Alpine.js for dynamic condition add/remove and serializes to hidden JSON fields on submit — no raw JSON editing for users
- The `{% load i18n %}` in the policy detail template is needed for `pluralize` filter usage in groups template (inherited via extends)
- 24 new URL routes, 6 new models, 2 migrations (schema + data), ~18 new view functions

## 2026-02-25 - [Deployment] Description field for AppPermissionRequest in Permissions Editor

**Conversation:** [2026-02-25-1358-64e65289.md](conversations/2026-02-25-1358-64e65289.md)

Added a `description` field to `AppPermissionRequest` so users and the permissions agent can document why permission changes are needed. The field is filled in by the user via a multi-line textarea in the PE, and/or by the agent via the `update_permission_draft` MCP tool.

**Model and service layer:**

- `AppPermissionRequest.description` — TextField, blank=True. Migration applied.
- `update_description(app_permission_request, description)` — sync, replaces the full description (used when the user saves the textarea).
- `amerge_description(app_permission_request, text)` — async, appends text with `\n\n` separator if existing content exists (used when the agent provides a description). Preserves user-authored text at the top.

**Cancel semantics:** Cancel resets `statements` back to baseline but leaves `description` untouched. The description is metadata about the request intent, not the policy itself — so a user who typed rationale then fat-fingered a service and hit cancel keeps their description.

**PE UI:** Expandable panel (same style as service groups) between the statements container and cancel/submit buttons. Textarea with debounced htmx POST (`hx-trigger="input changed delay:1s"`) — `changed` ensures we only persist when there are new keystrokes. Panel defaults to expanded (`<details open>`).

**MCP tool:** Optional `description` parameter on `update_permission_draft`. When provided, the agent's text is merged via `amerge_description`. Tool description updated to explain the append behavior.

**Security hub accordion:** Applied/Failed APRs now show the description in the accordion panel, with a "Description:" label above the text, matching the style of Service/Resources/Access levels.

**SSE refetch:** The `doh:permissions-changed-{id}` event listener fetches the description via a new GET endpoint and updates the textarea — but only when the textarea is not focused, to avoid clobbering in-progress user typing.

**Key points:**
- New endpoints: `security_permissions_editor_description` (GET, plain text) and `security_permissions_editor_update_description` (POST, 204)
- Description panel has a "Description:" title in the security hub accordion for Applied/Failed rows
- `TOOL_INPUT_PARAMS_FOR_TITLE` stays as `service` for `update_permission_draft` since description is secondary context

## 2026-02-25 01:30 - [Deployment] S3 prefix input for permissions editor

**Conversation:** [2026-02-25-0053-8af2e350.md](conversations/2026-02-25-0053-8af2e350.md)

Added a prefix input field to the S3 service group in the permissions editor so users can scope object-level permissions to a specific path (e.g., `data/uploads/*`) instead of defaulting to `*` (all objects). This is S3-only — other services don't have the concept of object path prefixes in their ARNs.

**Architecture — split bucket ARN from prefix:**

`_list_s3_resources` now returns base bucket ARNs (`arn:aws:s3:::my-bucket`) without the `/*` suffix. The prefix is a separate input in the UI. When the user selects a bucket from the dropdown, the view combines them: `{bucket_arn}/{prefix}`. This keeps the stored resource ARN as a standard IAM resource pattern (e.g., `arn:aws:s3:::my-bucket/data/*`) while giving the user explicit control over the prefix.

**S3 "selected" matching uses startswith:**

Since the dropdown shows base bucket ARNs but stored resources include the prefix, the "selected" checkmark in the dropdown uses `startswith` matching: a bucket shows as checked if any stored resource starts with `bucket_arn/`. This handles multiple prefixes for the same bucket — the bucket shows checked when any of its resources are selected.

**S3 dropdown removal is bucket-scoped:**

Clicking a checked S3 bucket in the dropdown sends `remove_resource` with the base bucket ARN. The view handles this specially: instead of exact-match removal (which would miss prefixed ARNs), it removes all stored resources matching `r == base_arn or r.startswith(base_arn + "/")`. This is done directly in the view rather than the service layer to keep S3-specific logic out of the generic `update_statements` function.

**Alpine state preserves prefix across HTMX swaps:**

The prefix input uses `x-model="s3Prefix"` bound to the Alpine `x-data` scope on the parent div (which is NOT inside the `resources` partialdef). Since HTMX only swaps the inner `#resources-{service}` div, the Alpine scope survives and the user's typed prefix persists across add/remove operations.

**Key points:**
- Service-specific placeholder text (`RESOURCE_PLACEHOLDERS` dict) — "Select S3 bucket...", "Select SQS queue...", etc. for 10 curated services, falling back to "Select resource..." for others
- Dropdown close-on-reclick: changed `@focus` to `@mousedown` toggle so clicking the input when the dropdown is open closes it, with `@focus` as fallback for keyboard navigation
- The prefix input only renders for S3 (`{% if group.service == "s3" %}`), and the Alpine `x-data` conditionally includes `s3Prefix` only for S3 to avoid wasted state on other services
- Width layout: bucket selector is `w-50` (compact, just shows bucket name) and prefix input is `flex-1` (takes remaining space), visually separated by a `/` character

## 2026-02-25 - [UI] Accordion for Applied/Failed permission requests on Security hub

**Conversation:**

Added an accordion to the Permission Requests list on the Security hub page. Applied and Failed APRs now expand in-place to show a compact summary of what was applied, instead of navigating to the permissions editor. Draft, Approved-Pending-Apply, and Applying APRs retain the existing click-to-navigate behavior.

**Implementation — template-only, no view changes:**

The `statements` JSONField is already on the queryset (fetched via `select_related` in the `security()` view), so the accordion content renders inline with zero additional HTTP requests. Alpine.js `x-data="{ openId: null }"` on the list container ensures only one row is open at a time — clicking a row toggles `openId`, and any previously open row collapses.

**Accordion height animation without Alpine collapse plugin:**

The project loads core Alpine.js only (no plugins). Used the CSS grid trick for smooth height transitions: a wrapper with `grid-template-rows: 0fr` (collapsed) transitioning to `1fr` (expanded), with an inner `overflow-hidden` div. This gives a true vertical expand/collapse animation purely with CSS transitions, no JS height calculation needed.

**Compact statement summary format:**

Each service block shows three labeled lines — `Service: s3`, `Resources: arn:..., arn:...` (monospace, comma-separated), `Access levels: Read, Write` (indigo text). Services are separated with `space-y-3`. Uniform `space-y-1` within a service block ensures consistent vertical rhythm since all rows are plain `<p>` elements.

**Key points:**
- Applied/Failed rows render as `<button>` (not `<a>`) to avoid navigation; other statuses keep `<a>` with `hx-get` to the permissions editor
- Accordion panel uses `dark:bg-gray-900` against the parent card's `dark:bg-gray-800` for visible contrast in dark mode
- Chevron icon rotates 90° when expanded via Alpine `:class` binding
- Tried indigo pills for access levels (matching the editor) but reverted to plain text — the pills' internal padding (`py-0.5`) broke vertical spacing consistency between the three label lines

## 2026-02-24 22:00 - [Deployment] Permissions apply executor — job worker applies approved IAM policies

**Conversation:**

Implemented the missing piece in the permissions flow: applying approved permission changes to the actual IAM role in AWS. Previously, clicking "Apply" in the permissions editor set the `AppPermissionRequest` status to `APPROVED_PENDING_APPLY` but nothing picked it up — the IAM role was never modified.

**Architecture — follows established job worker pattern:**

Added a new executor (`permissions_apply_executor.py`) that the job worker polls for and spawns in a thread, identical to how deployments, environment provisioning, and teardowns work: `_claim_pending_permissions_apply()` atomically transitions APPROVED_PENDING_APPLY → APPLYING via `select_for_update(skip_locked=True)`, then a thread runs `run_apply()` which calls `put_role_policy()` and transitions to APPLIED or FAILED.

**IAM policy generation — forward mapping with policy_sentry:**

The existing `read_app_permissions_policy` reverse-maps IAM actions to access levels (e.g., `s3:GetObject` → "Read") using `_actions_to_access_levels`. The new `write_app_permissions_policy` does the inverse: expands access levels back to IAM actions using `get_actions_with_access_level(service, level)`. For example, S3 "Read" + "Write" expands to 112 individual actions. Tested that the resulting policy document is ~3700 chars for 2 services, well under the 10,240 char inline policy limit.

Empty statements are handled by deleting the inline policy rather than writing an empty one (IAM doesn't accept empty statement lists).

**Baseline update moved from approve() to post-apply:**

Previously `approve()` eagerly updated `AppPermissions.statements` (the baseline reflecting what's in AWS) at the moment of approval. This was incorrect — if the IAM apply failed, the baseline would diverge from AWS reality. Now `approve()` only transitions the status; the executor updates the baseline after `put_role_policy()` succeeds. This means "cancel" during a failed apply correctly reverts to what's actually in AWS.

**Key points:**
- `_build_iam_policy_document()` skips statements with no service or no access levels, and defaults to `["*"]` for resources when none are specified
- `write_app_permissions_policy()` handles the empty-statements case by calling `delete_role_policy()` with a `NoSuchEntityException` guard (idempotent)
- `approve()` signature simplified: no longer takes `app_permissions` parameter since it doesn't touch the baseline
- The executor uses `permissions_service.get_or_create_app_permissions()` to fetch the baseline for update, reusing the existing seed-from-AWS-on-first-access logic

## 2026-02-25 - [AgentChat] Unwrap MCP content at source, simplify tool result pipeline

**Conversation:** [2026-02-24-1958-f85ab96e.md](conversations/2026-02-24-1958-f85ab96e.md)

MCP tool results were flowing through the system in raw wire format (`[{"type": "text", "text": "<json>"}]`) and every consumer had to independently unwrap them via `extract_mcp_text_content`. This created duplicated parsing logic in the streaming views, template filters, and test harness.

**Core change:** Added `_unwrap_mcp_content` in `agent_service.py` that converts MCP content blocks into plain dicts or strings at the source — inside `_handle_tool_results`, right after receiving `block.content` from the SDK. Both the persisted `Message.metadata["result"]` and the `AgentStreamEvent.data["result"]` now carry clean domain data. Only applied to `mcp__*` tools; built-in SDK tools (Bash, etc.) return plain strings as `block.content`, so they pass through unchanged.

**Key points:**
- Deleted `extract_mcp_text_content` entirely — was called in 4 places (chat.py streaming, chat_filters.py json_pretty, chat_filters.py tool_result_get_parsed, test_main_agent.py). All eliminated.
- Removed `tool_result_get_parsed` template filter — was just `metadata.get("result", "")` after simplification. Template now uses `message.metadata.result` directly.
- `chat_filters.py` went from 147 lines to 73 — the MCP unwrapping layers, double `json.loads` chains, and defensive `try/except` blocks are all gone.
- Fixed `sanitize_paths_for_display` to use `re.sub` for substring matching instead of only handling strings that start with `/`. Sandbox paths embedded mid-string (e.g., in error messages) are now sanitized too.
- Tool parameters in streaming UI now use `json_pretty` template filter instead of pre-formatted `json.dumps` in the view, so they also get path sanitization.
- Added `SANITIZE_SANDBOX_PATHS` setting to toggle path sanitization on/off.
- Renamed `get_tool_main_param` → `get_tool_input_param_for_title`, `TOOL_MAIN_PARAMS` → `TOOL_INPUT_PARAMS_FOR_TITLE`, and `_format_param_value` → `_format_input_param_title` (moved closer to its single caller) for clarity.
- Also updated permissions agent system prompt to not proactively run analysis on conversation start.

## 2026-02-25 - [AgentChat] Replace cross-component OOB swaps with SSE notify-and-refetch pattern

**Conversation:** [2026-02-24-1729-d6f5988d.md](conversations/2026-02-24-1729-d6f5988d.md)

The chat SSE stream was sending fully-rendered OOB HTML to update components outside the chat panel (conversation title, sidebar title/cost, permission statements editor). This coupled the server to every consumer's markup and required the chat JS to manually process OOB elements for cross-component targets. Replaced with a lightweight notification pattern where the server sends a JSON event and each interested component refetches its own content.

**Architecture — three layers:**

1. **Server sends `sse-notify`** — just a JSON payload like `{"type": "title-changed", "id": "abc123"}`, no rendered HTML. Added `_format_sse_notify` helper. Removed `_render_title_update`, `_render_cost_update`, and the `render_statements_oob_html` call from tool results. Also removed the `sync_to_async` wrapper for tool_result events since the DB query for permission statements rendering is gone.

2. **JS handler bridges SSE scope to document** — `handleNotify` in `_chat_panel.html` parses the JSON and dispatches `doh:{type}-{id}` on `document`. The `-{id}` suffix means only the matching entity's elements react. The SSE connection lives on `#messages` (confined to the chat panel), so the document-level dispatch is necessary for elements outside that subtree.

3. **Components self-refetch** — Header title uses declarative `hx-trigger="doh:title-changed-{id} from:document"` + `hx-get`. Sidebar title/cost use the same pattern. Permission statements editor uses a JS listener with `htmx.ajax()` (needs the dynamic URL and also enables Apply/Cancel buttons).

**New thin GET endpoints** return minimal fragments: `/chat/{id}/title/` (plain text), `/chat/{id}/cost/` (cost span HTML), `/security/permissions/{id}/statements/` (rendered statements template).

**Key learnings:**

- **HTMX attribute inheritance is a trap for nested hx-* elements** — sidebar `<h3>` and `<p>` elements with `hx-get`/`hx-trigger` sat inside an `<a>` tag with `hx-push-url="true"` and `hx-target="#chat-panel"`. The title refetch navigated the page instead of doing an in-place swap. Fixed with `hx-disinherit="*"` on the `<a>`. Considered `htmx.config.disableInheritance` globally but rolled back because it breaks CSRF token delivery via `hx-headers` on `<body>`.

- **OOB swaps are fine for same-component updates** — thinking indicator reset and tool spinner→result replacement stay as OOB because the producing template owns the target element. Cross-component OOB was the anti-pattern.

- **`doh:oob-swap` event removed** — was dispatched by `processOobElements` after each swap so external components could react. No longer needed since those consumers now use the targeted `doh:{type}-{id}` events instead.

## 2026-02-24 22:30 - [AgentChat] Add `update_permission_draft` MCP tool — agent can now modify the draft policy

**Conversation:** [2026-02-24-1527-f9772ca7.md](conversations/2026-02-24-1527-f9772ca7.md)

Added an MCP tool that lets the permissions agent directly add/update permission statements in the draft policy, rather than only advising the user to make changes manually. When the agent detects missing permissions (from source code analysis, CloudWatch Logs, or CloudTrail), it calls `update_permission_draft` and the editor panel updates in real-time via OOB HTML swap.

**Architecture — iterative simplification:**

The implementation went through several design iterations, each simplifying the previous approach:

1. **Started with `pending_oob` plumbing** — a list threaded from `create_devopshero_mcp_server` through `_create_agent_options`, stored on `MainAgent`, drained in `stream_turn`. The MCP tool rendered OOB HTML and appended it. This was over-engineered: the list was always created empty and immediately passed in, so it could just live inside the MCP server factory.

2. **Moved to `pending_events` signals** — the MCP tool published `{"type": "permission_draft_updated", "apr_id": ...}` and chat.py handled rendering. Better separation but still unnecessary plumbing through `agent_service.py`.

3. **Removed all plumbing** — realized `_render_streaming_tool_result` in chat.py already has the tool name and parsed result. Just check the tool name there and render OOB HTML. No events, no pending lists, no changes to `agent_service.py` at all.

4. **Considered moving OOB render into the MCP tool's `asyncio.to_thread`** to avoid the async/sync issue in chat.py, but rejected it because it couples the service layer (mcp_tools) to the view layer (security_views). Instead, tool_result events go through `sync_to_async` in the SSE generator.

**Key design decisions:**

- **`doh:oob-swap` custom event** — `processOobElements` in `_chat_panel.html` used to have hardcoded knowledge of `permission-statements-container` and `conversation-title`. Replaced with a generic `document.dispatchEvent(new CustomEvent('doh:oob-swap', { detail: { targetId } }))` after each swap. The permissions editor and chat sidebar each listen for their own target IDs. The chat panel is now fully generic — it just does the DOM swap and fires the event.

- **`upsert_statement` merge semantics** — unions access_levels and resources into the existing statement for a service, creating if absent. Never duplicates. This lets the agent call the tool multiple times for the same service without worrying about state.

- **Tool result includes `apr_id`** — the view layer needs the APR ID to render the OOB HTML. Including it in the MCP tool response means the data flows naturally through the existing tool result pipeline.

**Python import gotcha with `__init__.py`:**

`views/__init__.py` does `from .security import security` which creates an attribute `security` on the package bound to the view *function*. This shadows the `security` *module*. Every import form that resolves through the package namespace (`from . import security`, `import devopshero_app.views.security as x`) gets the function, not the module. Even `import X.Y.Z as alias` walks the attribute chain and hits the shadowed name. The only reliable ways to get the module are `importlib.import_module` or `sys.modules` — both ugly. We settled on `from .security import render_statements_oob_html` (importing the function directly by name).

**Async/sync boundary:**

`_render_streaming_tool_result` is a sync function called from an async SSE generator. The existing `render_to_string` calls work because they don't hit the DB. But `render_statements_oob_html` loads the APR and fetches available resources (DB queries). Solution: `tool_result` events specifically go through `sync_to_async(_format_sse_event, thread_sensitive=True)` in the generator. All other event types stay on the fast path.

## 2026-02-24 06:45 - [AgentChat] Permissions agent runtime error detection tools (CloudWatch Logs + CloudTrail)

**Conversation:** [2026-02-23-2231-75de2430.md](conversations/2026-02-23-2231-75de2430.md)

Added two new MCP tools for the permissions agent to detect IAM permission denials at runtime, complementing the existing source code analysis. The agent now has three signals to work with: static code analysis (what permissions the code *needs*), CloudWatch Logs (what errors the app is *logging*), and CloudTrail (what API calls AWS *denied*).

**Tools created:**

- `query_app_logs` — Queries CloudWatch Logs `FilterLogEvents` on the app's ECS log group (`/devopshero/{env.slug}/ecs`) with the app slug as stream prefix. Uses a multi-term OR filter pattern (`?"AccessDenied" ?"is not authorized to perform" ?"AuthorizationError"` etc.) to catch common AWS permission error formats. Results are **compacted**: raw events are grouped by an extracted error signature (error code + API operation + resource ARN via regex) so the agent sees each distinct denial once with a count and time range, instead of 50 copies of the same error. This was a key design decision — agents have limited context, so deduplication directly improves analysis quality.

- `lookup_access_denied_events` — Queries CloudTrail `LookupEvents` for AccessDenied management events from the app's task role. Client-side filters by error code and role ARN since CloudTrail doesn't support server-side filtering on error codes. Paginates up to 10 pages with 0.5s sleep for rate limiting. Always includes a note about the data events limitation (S3 GetObject, DynamoDB PutItem require separate CloudTrail data event logging).

**Tool scoping for PERMISSIONS mode:**

In `_create_agent_options()`, permissions conversations now get a restricted tool set: only `Read`, `Glob`, `Grep` as built-in tools (no Write, Edit, Bash, Task) and only `query_app_logs`, `lookup_access_denied_events`, `wait` as MCP tools. No deployment/git/infrastructure tools, no sub-agents. This prevents the permissions agent from accidentally deploying or modifying code.

**Key learnings:**

- CloudWatch `FilterLogEvents` returns many empty pages while scanning log streams — a 24h window with few matches can take 10+ API calls returning 0 events before finding results. Considered early termination (stop after finding events then hitting an empty page) but rejected it because results can be spread across non-contiguous pages from different log streams. `MAX_PAGES=20` is the only safe cap.
- The filter pattern needed widening: original had `?"AccessDeniedException"` which misses the bare `AccessDenied` error code boto3 uses in `(AccessDenied)`. Changed to `?"AccessDenied"` (substring match catches both). Also added `?"AuthorizationError"` (SNS/SQS) and `?"ExpiredToken"`.
- Both tools reuse `iam_utils._get_aws_session_for_environment()` for cross-account role assumption. The `AppPermissionRequest` loaded with `select_related("app", "environment", "environment__aws_account")` provides all needed context — no additional DB queries.

## 2026-02-23 23:15 - [DomainModel] Reverse Conversation ↔ AppPermissionRequest FK direction + permissions system prompt

**Conversation:** [2026-02-23-1632-478b7885.md](conversations/2026-02-23-1632-478b7885.md)

Reversed the FK relationship between `Conversation` and `AppPermissionRequest` to match the established pattern used by other context FKs on Conversation (`context_repository`, `context_aws_account`). Previously `AppPermissionRequest` had a nullable FK pointing to `Conversation`; now `Conversation` has a `context_app_permission_request` FK pointing to `AppPermissionRequest`. The Conversation points to what it's about, not the other way around.

This also fixed a real bug in the `security_permissions_editor` view: when creating conversations for the permissions chat panel, `repo_id` and `aws_account_id` were passed as `None`. Now they're correctly derived from the app and environment (`app.repository_id`, `environment.aws_account_id`), giving the agent access to the repository for source code analysis.

**Key points:**

- Added `context_app_permission_request` FK (nullable, `SET_NULL`) to `Conversation` using a string reference `"AppPermissionRequest"` since the model is defined later in models.py
- Removed `conversation` FK from `AppPermissionRequest` — the conversation lookup in `security.py` now uses `Conversation.objects.filter(context_app_permission_request=...)` instead of `app_permission_request.conversation`
- Added `app_permission_request_id` as a required (no default) parameter to `create_conversation()` — all four callers (`chat_new`, `chat_app_deploy`, `test_main_agent`, `security_permissions_editor`) explicitly pass it. The plan initially had a default value of `None` but this was removed per code review to keep the signature explicit.
- Created `system_prompt_permissions.md` base prompt describing the permissions assistant role, capabilities (source code analysis, draft review, transitive dependency inference, blast radius assessment), and guidelines
- Added `_build_permissions_prompt()` in `agent_service.py` that loads the base prompt and appends XML context sections: `<conversation_context>` (app, environment, task role name) and `<current_draft_statements>` (current draft JSON). Uses `select_related` to avoid N+1 queries. Task role name computed as `doh-{env.slug}-{app.slug}-task-role`[:64] matching the CDK naming convention.
- Had to fix admin.py before running makemigrations — Django's system checks caught the stale `"conversation"` autocomplete field on `AppPermissionRequestAdmin` before it would generate the migration

## 2026-02-23 21:30 - [UI] App detail page deployment rows redesign

**Conversation:**

Redesigned the deployment rows on the app detail page to fix alignment issues and reduce visual clutter. The original design had several problems: status badges wrapped in bordered boxes (card-within-a-card), "Redeploy" and "Permissions" as loose text links with no structure, and inconsistent layouts between the "Deployed to Environments" and "Recent Deployments" sections.

**Key changes:**

- Removed the bordered box around status+timestamp in both sections — the colored pill badge is sufficient, wrapping it in another container was double-framing. Status and timestamp are now inline: `[Deployed] · 3h ago`
- Replaced the three-dot dropdown menu with a plain "Tear Down" text link — the dropdown was overkill for a single action. The link still triggers the same confirmation modal. Styled in indigo like other links (not red) since the confirmation modal handles the "are you sure" concern.
- Switched both sections from `flex justify-between` to CSS grid with fixed columns (`grid-cols-[1fr_auto_5rem]`) so the info, status, and actions columns align consistently across all rows regardless of content
- Moved "New Deployment" button from the "Deployed to Environments" header up to the page header (where the app type pill was), and moved the pill inline next to the "Created" date under the title
- Consolidated the "Deployed to Environments" inline template code into a reuse of `_app_deployment_row.html` with a new `mode="app_summary"` — this was the key fix for alignment since both sections now share the exact same grid template and column widths
- When a deployment row has no action links (non-deployed status), a centered em-dash placeholder (`&mdash;&mdash;&mdash;`) reserves the column width to maintain alignment
- The `app_summary` mode now shows the same heading format as the default mode ("Environment: dev" with Account/Region and Ref lines) for consistency

## 2026-02-19 16:26 - [DomainModel] AppPermissions model + Cancel feature for permissions editor

**Conversation:** [2026-02-19-1627-9e601f6c.md](conversations/2026-02-19-1627-9e601f6c.md)

Introduced `AppPermissions` as the DB-side source of truth for an app+environment's current (last-applied) IAM permissions. Previously, the permissions editor read IAM policies from AWS every time a new draft was created, making "cancel" impossible — there was no stored baseline to revert to.

**AppPermissions model:** Stores `statements` (JSONField) per app+environment pair with a `unique_together` constraint. On first access, seeds from AWS via `iam_utils.read_app_permissions_policy()` (best-effort, empty list on failure). This is a one-time migration — after that, the DB is the source of truth.

**Flow changes:**
- `get_or_create_app_permissions()` fetches or creates the baseline (seeds from AWS on first access)
- `get_or_create_draft()` now copies from `AppPermissions.statements` instead of calling AWS directly
- `approve()` updates `AppPermissions.statements` to match the approved request, so the baseline advances
- `cancel()` resets the draft's statements back to the `AppPermissions` baseline

**Cancel button UX evolution** — went through several iterations:
1. Started with `hidden` class (display:none) → caused layout shift when appearing (Apply button jumped down a few pixels)
2. Switched to `invisible` (visibility:hidden) → button reserves space, no layout shift
3. Tried opacity fade-in/fade-out (50ms, then 100ms) → user decided against it
4. Final: always visible, **disabled/enabled** state. Both Cancel and Submit Request start `disabled` (greyed out at 50% opacity), become enabled after first mutation, Cancel re-disables both

**Cancel swap target narrowing:** Initially the cancel view re-rendered the full editor template (`hx-target="#main-content"`), which caused a brief flicker in the chat panel as it was torn down and rebuilt. Fixed by targeting only `#statements-container` and rendering just the `_permission_statements.html` partial. The cancel button hides itself via `hx-on::after-request`.

**Show-after-mutation approach (hybrid server+client):** Server controls initial disabled state via `has_changes` (compares `app_permission_request.statements != app_permissions.statements`). A JS `htmx:afterRequest` listener enables both buttons after any successful POST to the update-statement URL. This avoids complex OOB swaps from the update_statement view, which returns varied response shapes (partials, empty HttpResponse, sub-fragments).

**Key points:**
- `invisible` vs `hidden` vs `opacity-0` vs `disabled` — each has different layout/interaction trade-offs. `disabled` was the right choice here: buttons are always visible as affordances, just greyed out
- Narrowing HTMX swap targets prevents unnecessary DOM teardown — only swap what actually changes
- Hybrid server+client visibility control works well when the mutation endpoint has varied response shapes that make OOB swaps impractical
- Renamed "Apply" → "Submit Request" with matching states ("Submitting...", "Submitted", "Failed")

## 2026-02-20 00:30 - [Bugfix] Resources dropdown flicker on close when search filter is active

**Conversation:**

When the resources combobox had an active search filter (narrowing the visible items) and the user clicked outside to close it, the full unfiltered list would briefly flash before the dropdown disappeared.

**Root cause:** The dropdown panel uses `x-transition.opacity.duration.50ms` for a fade-out animation. The original `@click.outside` handler did `open = false; search = ''` in the same expression. Clearing `search` makes every item's `x-show="!search || ..."` evaluate to `true`, so all items become visible. But the panel isn't hidden instantly — it fades out over 50ms. During that 50ms fade, the now-unfiltered full list is visible. This happens regardless of `$nextTick` ordering because the transition delay means the panel is still partially opaque when the search clear takes effect.

**Fix — separate the clear from the close, using two principles:**

1. **On close (click-outside, Escape):** set `open = false` only. Clear `search` via `setTimeout(() => search = '', 60)` — the 60ms delay exceeds the 50ms transition, so the panel is fully hidden before items reappear.
2. **On open (`@focus`):** clear `search` immediately so the filter is always fresh when the dropdown opens.

This means the search text in the input also gets cleaned up after closing (the user's follow-up request), but safely after the panel is invisible.

**Key points:**
- `x-transition` delays mean any state that affects child visibility must not be reset during the fade-out window
- `$nextTick` doesn't help here because it fires after Alpine's reactive flush but before CSS transitions complete — the panel is still mid-fade
- The pattern "reset state on open, not on close" avoids an entire class of transition-related flicker bugs
- `setTimeout` with a duration slightly longer than the transition is a pragmatic escape hatch when Alpine's reactive model conflicts with CSS transition timing

## 2026-02-19 13:24 - [UI] Permissions editor: Resources dropdown redesign as combobox with search

**Conversation:** [2026-02-19-1325-9efa9084.md](conversations/2026-02-19-1325-9efa9084.md)

Redesigned the Resources dropdown in the permissions editor to replace the native `<select>` element with a custom Alpine.js combobox that visually matches the Access Levels dropdown and adds search/filter plus manual ARN entry.

The previous implementation used a plain HTML `<select>` for resource selection, a separate list of resource chips below, and a standalone manual ARN input with an Add button. This looked inconsistent next to the Access Levels dropdown which used a custom Alpine dropdown with chips, checkboxes, and a chevron. The new design unifies these into a single combobox pattern.

**Design evolution through iteration:**

The final design went through several rounds of refinement. Initially the resource chips were placed inside the trigger box (matching Access Levels exactly), but ARNs are much longer than access level names like "Read" or "Write", so chips were moved back below the dropdown as a separate list. The dropdown initially had a separate search input at the top and a separate manual ARN input at the bottom — these were merged into a single input that doubles as the trigger, the filter, and the ARN entry point. The placeholder dynamically switches between "Select resource..." (closed) and "Filter or paste resource ARN..." (open) using Alpine's `:placeholder` binding.

**Key points:**
- Created `devopshero_app/templatetags/aws_filters.py` with an `arn_short_name` filter that extracts the resource portion of an ARN (everything after the 5th colon). This shows `my-bucket/*` instead of the full ARN in chips, while preserving the resource type prefix (e.g. `table/my-table` for DynamoDB) which matters when a service has multiple resource types. Initial implementation incorrectly split on `/` which stripped bucket names from S3 ARNs like `arn:aws:s3:::my-bucket/*` → `*`.
- The combobox input serves triple duty: (1) clicking it opens the dropdown via `@focus="open = true"`, (2) typing filters the resource list via Alpine `x-show` with `data-filter` attributes for case-insensitive contains matching, (3) pasting an ARN starting with `arn:` reveals an inline emerald Add button via `x-show="search.startsWith('arn:')"`. Enter key also submits but only when the value starts with `arn:` (htmx trigger condition).
- The `x-data="{ open: false, search: '' }"` scope lives on the wrapper div outside the HTMX swap target `#resources-{service}`, so Alpine state (dropdown open, search text) survives fragment swaps — consistent with the pattern established for Access Levels and documented in AGENTS.md.
- Added `autocomplete="off"` to suppress browser autocomplete popups that interfered with the custom dropdown. Added Escape key handler to close dropdown, clear search, and blur the input. Added a heavy custom shadow (`shadow-[0_10px_50px_-5px_rgba(0,0,0,0.5)]`) to visually lift the dropdown from the page surface.
- Dropdown items show full ARNs in monospace font for precision, with checkbox-style SVGs (emerald for selected, gray for unselected) matching the Access Levels pattern. Clicking a selected resource removes it (toggle behavior).

## 2026-02-19 22:15 - [Deployment] Fix CREATE_ROLLBACK_COMPLETE stacks blocking re-deployment

**Conversation:** [2026-02-19-1307-c1189ea2.md](conversations/2026-02-19-1307-c1189ea2.md)

CloudFormation stacks that fail during creation land in `ROLLBACK_COMPLETE` — they still "exist" as far as the API is concerned but cannot be updated or reused. CDK will refuse to deploy over them with an error like "Stack is in ROLLBACK_COMPLETE state and cannot be deployed." The fix is to detect and delete them before every deploy attempt.

`deploy_base` already had `cleanup_rollback_complete_stacks` for the three base stacks (vpc, cluster, builder), but `deploy_app` had no equivalent. If an app's ECR, app, or Aurora stack failed on first create, subsequent deployments would always fail until someone manually deleted the stack.

**Key points:**
- Moved `cleanup_rollback_complete_stacks` from `deploy_base` to `cloudformation_utils` — it only calls `get_stack_status` and `delete_stack_and_wait`, both of which live there, so that's the right home.
- Generalized the function signature from `(cf_client, env_slug: str)` (with hardcoded names inside) to `(cf_client, stack_names: list[str])` so it's reusable by any caller.
- `deploy_base.deploy()` now defines the three base stack name variables at the top of the function (before the cleanup call) and reuses them for the CDK stack constructors further down — eliminating the previous duplication where the same f-strings appeared twice.
- `deploy_app.deploy()` now calls the same cleanup for `{prefix}-ecr`, `{prefix}-app`, and conditionally `{prefix}-aurora` (only if `app_config.database_config` is set) before verifying that base infrastructure exists.

## 2026-02-19 21:30 - [UI] Fragment-level HTMX swaps for permissions editor using Django 6.0 partialdef

**Conversation:** [2026-02-19-1211-f9681f3e.md](conversations/2026-02-19-1211-f9681f3e.md)

Replaced the whole-service-group `morph:outerHTML` swap strategy in the permissions editor with fragment-level `innerHTML` swaps using Django 6.0's `{% partialdef %}` / `{% partial %}` tags. Previously, every mutation (add/remove resource, toggle access level) replaced the entire `#service-group-{service}` div, which destroyed Alpine's `x-data` scope and required the `window._alReopen` hack to reopen the dropdown, plus a `style="display: none"` hack to work around idiomorph stripping Alpine's inline styles.

Now: resource mutations swap only `#resources-{service}` (innerHTML), level mutations swap only `#access-levels-{service}` (innerHTML). The Alpine `x-data="{ open: false }"` wrapper is never replaced, so dropdown state survives naturally. Both hacks are removed.

**Three Django 6.0 partialdef lessons learned the hard way:**

1. **`partialdef` does NOT render inline.** Unlike `{% block %}`, `{% partialdef name %}` only *defines* a named fragment — it doesn't output anything where it appears. You must use `{% partial name %}` to render it. This matches the pattern in `_message.html` where the partialdef is at the bottom and `{% partial %}` calls are above. The initial attempt put content directly inside `partialdef` expecting inline rendering, which produced empty divs.

2. **Hyphenated names are invalid.** `{% partialdef access-levels %}` is parsed as `access` minus `levels` by the Django template engine, causing a `TemplateSyntaxError` that silently breaks the entire template. All existing partials in the project use underscores (`dropdown_select`, `message_dispatcher`, `navigation_item`). Changed to `access_levels`.

3. **Partial renders only process the partialdef block.** When the view renders `template.html#resources`, Django only processes the content inside `{% partialdef resources %}`. Template tags outside the block (like `{% url ... as update_url %}` at the top of the file) are NOT executed. This meant `update_url` was empty in the partial response, breaking all `hx-post` URLs after the first swap. Fixed by adding `{% url %}` inside each partialdef.

**View changes:** The `security_permissions_editor_update_statement` view now routes to the correct partial based on action: `add_resource`/`remove_resource` render `#resources`, `add_level`/`remove_level` render `#access_levels`, `remove_service` unchanged (still uses `hx-swap="delete"`).

**Key points:**
- `{% partialdef %}` defines, `{% partial %}` renders — they are always used as a pair
- Partial names must be valid Python identifiers (no hyphens)
- When rendering `template.html#partial_name`, only the partialdef block is processed — any template tags outside it (variable assignments, url lookups) must be duplicated inside each partialdef that needs them
- `innerHTML` swap on a wrapper div preserves the wrapper's Alpine scope, eliminating the need for state-restoration hacks

## 2026-02-19 18:15 - [Bugfix] Idiomorph strips Alpine's x-show inline style during morph

**Conversation:** [2026-02-19-1028-30ebb814.md](conversations/2026-02-19-1028-30ebb814.md)

The access levels dropdown in the permissions editor was popping open whenever a resource was added or removed. The initial hypothesis (stale `window._alReopen` flag leaking across operations) was wrong — adding an `htmx:afterSettle` cleanup handler didn't fix it.

**Root cause.** Alpine implements `x-show="open"` by setting `style="display: none"` on the element when `open` is `false`. The server-rendered HTML didn't include that inline style — it only had `x-show="open" x-cloak`. During `morph:outerHTML`, idiomorph diffs old DOM vs new HTML: it sees the old element has `style="display: none"` (set by Alpine) but the new HTML has no style attribute, so it **removes** the inline style. The element becomes visible. Alpine's reactive system doesn't notice because `open` is still `false` — nothing triggered a re-evaluation of `x-show`.

**Fix.** Added `style="display: none"` directly in the template on the dropdown panel div. Now idiomorph sees the same style in both old and new HTML and leaves it alone. When Alpine initializes, it removes `x-cloak` and takes over via `x-show`, which also says "hidden" since `open` starts `false`. No conflict.

**Alpine morph alternative considered.** Alpine has its own morph plugin that understands `x-show`, `x-data`, etc. natively. Using it via the htmx `alpine-morph` extension would fix this class of problem at the root. Decided against it for now — the inline style fix is surgical, and this is the only Alpine component being morphed. Worth revisiting if more Alpine+morph interactions appear.

**Key points:**
- `x-cloak` is a CSS-based hide (`[x-cloak] { display: none !important }`) that Alpine removes on init — it prevents FOUC on page load but is gone by the time morphs happen
- Idiomorph treats inline styles as regular attributes to diff — it has no awareness of Alpine's reactive style management
- When mixing Alpine `x-show` with idiomorph, always include the default inline style in server HTML so morphs preserve it

## 2026-02-19 17:45 - [DomainModel] AwsResourceCache — DB-backed cache for AWS resource listings

**Conversation:** [2026-02-19-0150-30ebb814.md](conversations/2026-02-19-0150-30ebb814.md)

Added an `AwsResourceCache` model to avoid hitting AWS APIs (STS AssumeRole + List* calls) on every HTMX interaction in the permissions editor. The cache is per-environment (not per-PermissionRequest) because AWS resources belong to the infrastructure — two users editing different apps in the same environment see the same S3 buckets.

**How it works.** `permissions_service.get_resources_for_services()` queries the cache table first, identifies which services are cache misses, calls `iam_utils.list_resources_for_services()` only for those, bulk-creates cache entries, and returns the combined result. A separate `refresh_resources_cache()` deletes and re-fetches everything for explicit refreshes.

**Views wired up.** Uncommented the `_fetch_available_resources` calls that were disabled in the editor views (initial load, statement mutations, and new service addition). The `_fetch_available_resources` helper now delegates to the cache-aware service function instead of calling `iam_utils` directly. Added a new `security_permissions_editor_refresh_resources` POST endpoint + "Refresh resources" button in the editor UI.

**No automatic expiry (intentional for now).** The `fetched_at` field is stored but not checked for staleness. Cache entries live until explicitly refreshed by the user or until the cache row is deleted. A TTL-based expiry was considered but deferred — for the permissions editor use case, stale-but-present resources are better than surprise AWS latency mid-editing.

**Key points:**
- `unique_together = [("environment", "service")]` ensures one cache row per service per environment
- `bulk_create` with `ignore_conflicts=True` handles race conditions if two requests try to cache the same service simultaneously
- Auto-increment PK (not UUID) — this is a cache table, not a domain entity
- Registered in Django admin with read-only fields for debugging

## 2026-02-19 15:00 - [UI] Per-service-group HTMX swaps in permissions editor

**Conversation:**

Refactored the permissions editor so each service group makes its own HTMX call and receives back only its own HTML, instead of re-rendering the entire statements container on every interaction.

**Why.** Previously, every mutation (toggle access level, add/remove resource, remove service) hit the same endpoint and returned `_permission_statements.html` — the full container with ALL service groups re-rendered. This was wasteful: changing one checkbox in S3 would re-render DynamoDB, Lambda, and every other group too. It also caused the idiomorph/Alpine cross-contamination bug documented in the previous entry.

**What changed:**

- Each service group div gets a unique `id="service-group-{{ group.service }}"`. All in-place mutations (levels, resources) now target that specific ID with `hx-swap="morph:outerHTML"` — only the affected group re-renders.
- **Remove service** uses `hx-swap="delete"` — HTMX removes the element from DOM client-side. The backend just does the DB mutation and returns an empty `HttpResponse()`. No HTML rendering needed at all.
- **Add service** uses `hx-swap="beforeend"` on `#permission-statements-container` — the backend returns just the new service group partial, appended to the end.
- The backend view (`update_statement`) now branches by action: `remove_service` returns empty 200, everything else builds and returns just the single affected `_permission_service_group.html`.

**Alpine simplification.** The global `htmx:beforeSwap` listener that scanned all Alpine instances to find which dropdown was open is gone. It was needed because all groups were re-rendered, destroying all Alpine state. Now only the clicked group re-renders, so other groups' Alpine state is naturally preserved. For the access level dropdown (which needs to stay open while toggling levels), a simple `@click="window._alReopen = '{{ group.service }}'"` on each dropdown button sets the reopen flag before the HTMX request fires. The existing `x-init` on the Alpine component picks it up after morph.

**Empty state handling.** JS manages the "No policy statements yet" placeholder: removed before `beforeend` append on add, re-inserted via `htmx:afterSettle` listener when the last service group is deleted. The placeholder has `id="statements-empty"` for targeting.

## 2026-02-19 13:30 - [UI] Alpine.js multi-select dropdown for access levels — idiomorph/Alpine conflict and resolution

**Conversation:** [2026-02-19-0005-a675f64f.md](conversations/2026-02-19-0005-a675f64f.md)

Replaced the native `<select>` for access levels in the permissions editor with a custom Alpine.js dropdown featuring checkboxes and inline chips. The dropdown stays open between selections, enabling quick multi-select without repeated open-close cycles.

**The idiomorph/Alpine conflict.** The original plan called for `morph:innerHTML` swaps (via idiomorph) to preserve Alpine's `open` state across HTMX responses. This fundamentally doesn't work. Idiomorph patches DOM attributes and triggers Alpine's MutationObserver in ways that corrupt reactive state — in our case, clicking a checkbox in dropdown 1 would cause dropdown 2 to expand. We tried multiple fixes: unique IDs on wrapper elements (`id="al-dropdown-{{ service }}"`), unique IDs on service group cards (`id="sg-{{ service }}"`), explicit save/restore hooks via `htmx:beforeSwap`/`htmx:afterSettle` with `Alpine.$data()`, and even extracting state to a global `window._alDropdownState` object with getter/setter factory functions. None of these solved the morph+Alpine conflict.

**The solution: plain `innerHTML` + `x-init` restore.** Access level toggle buttons use `hx-swap="innerHTML"` (no morph), which cleanly destroys and recreates Alpine components. A `htmx:beforeSwap` listener saves which dropdown was open (by service name) to `window._alReopen`. Each dropdown's `x-init` checks this variable and sets `open = true` if it matches, then clears it. Because `x-init` runs during Alpine's component initialization — before the browser paints — there is no flicker. Other mutations (remove service, add resource, etc.) still use `morph:innerHTML` since they don't involve Alpine state.

**Key points:**
- Idiomorph and Alpine.js are fundamentally incompatible for preserving local UI state — morph corrupts Alpine's reactive proxies and MutationObserver tracking, causing state to leak between components
- `requestAnimationFrame` for post-swap state restore causes visible flicker because it fires AFTER the browser paints; `x-init` runs BEFORE the first paint
- The `window._alDropdownState` global factory approach (getter/setter reading from a JS object) failed due to timing: when htmx injects HTML, Alpine's MutationObserver processes `x-data` attributes before `<script>` tags execute, so the factory function doesn't exist yet
- Moving the factory to `base.html` (before Alpine CDN) solves timing but pollutes the global template with page-specific code — rejected for cleanliness
- The final pattern (`htmx:beforeSwap` saves to `window._alReopen`, `x-init` reads and clears) is minimal, self-contained, and flicker-free
- Added `has_checked_levels` boolean to the service group context data to conditionally render inline chips vs placeholder text in the trigger area

## 2026-02-19 00:15 - [Bugfix] Permissions editor CSRF 403 on Add Service — duplicate header from htmx.ajax + hx-headers inheritance

**Conversation:**

Clicking "Add Service" in the permissions editor when the permission request had no statements produced a 403 "CSRF verification failed. Request aborted." The root cause was subtle: the JS code manually extracted the CSRF token from the body's `hx-headers` attribute and passed it as an explicit `headers: { 'X-CSRFToken': csrfValue }` to `htmx.ajax()`. But `htmx.ajax()` also inherits `hx-headers` from the body automatically (using `document.body` as the default source element). The browser's XMLHttpRequest spec combines case-insensitively matching headers (`x-csrftoken` from inheritance + `X-CSRFToken` from explicit) into a single value: `token, token`. Django's CSRF middleware compared this against the expected single `token` and rejected it.

The other mutation buttons (Remove service, Add Level, etc.) worked because they used declarative `hx-post` attributes which only inherit — no duplication. The bug only surfaced on "Add Service" because it was the sole `htmx.ajax()` call with explicit headers.

**Fix:** Removed the explicit `headers` parameter from the `htmx.ajax()` call. Also converted the Apply button from `fetch()` to declarative `hx-post` with `hx-on::before-request` / `hx-on::after-request` event handlers, eliminating the entire CSRF token extraction block (`CSRF_TOKEN`, `csrfValue`, `APPLY_URL`) from JS. Now zero JS code touches CSRF tokens — everything goes through htmx's inherited `hx-headers`.

Updated `views/AGENTS.md` with a second CSRF rule: don't manually extract CSRF tokens in JS for htmx requests, and prefer `hx-post` attributes over `htmx.ajax()`/`fetch()`.

**Key points:**
- XMLHttpRequest's `setRequestHeader` combines case-insensitively matching header names by appending with `, ` — so setting both `x-csrftoken` and `X-CSRFToken` produces `token, token` which Django rejects
- `htmx.ajax()` with no `source` option defaults to `document.body`, so it inherits `hx-headers` from the body just like attribute-based htmx requests
- The bug only appeared when statements were empty because that's the only state where "Add Service" (the htmx.ajax path) is the first action — when statements exist from IAM, users interact via the declarative `hx-post` buttons instead
- `hx-on::before-request` and `hx-on::after-request` are sufficient for button state management (disable, text change, color swap) — no need for fetch + try/catch

## 2026-02-18 21:30 - [DomainModel] Permissions editor: server-side resources + service extraction + dedicated IAM policy

**Conversation:** [2026-02-18-2043-c8255962.md](conversations/2026-02-18-2043-c8255962.md)

Large refactoring session on the permissions editor, touching IAM reads, service layer extraction, view optimizations, and resource fetching. All motivated by the same principle: the server should own the data, views should be thin, and unnecessary work should be eliminated.

**Dedicated `doh-app-permissions` IAM policy.** The editor previously read ALL inline policies from the ECS task role via `list_role_policies`, which surfaced CDK-generated infrastructure plumbing (`TaskRoleDefaultPolicy` with `ssmmessages`, `logs`, `secretsmanager`). Replaced with a single `get_role_policy(PolicyName="doh-app-permissions")` call. `NoSuchEntityException` → editor starts blank. The `policy_name` is passed as an explicit parameter from the service layer, not hardcoded in `iam_utils`.

**Extracted `services/permissions.py`.** Business logic was living in `security.py` views. Three operations moved to the service: `get_or_create_draft()` (PermissionRequest lifecycle + IAM read), `update_statements()` (add/remove service/level/resource mutations), `approve()` (status flip). The `DOH_APP_PERMISSIONS_POLICY_NAME` constant lives here. Views became thin HTTP handlers. The service is where future `put_role_policy` write-back will live, callable from a worker without HTTP dependency.

**Conversation creation moved to the view.** `get_or_create_draft` was creating a `Conversation` via `agent_service` — mixing permissions domain logic with agent/UI concerns. Now lazily created in `security_permissions_editor` when `permission_request.conversation is None`. The FK was already nullable.

**Server-side resource rendering.** Previously, JS made N separate HTTP requests to a `/resources?service=X` endpoint (one per service group), each assuming the AWS role independently — 3 service groups = 3 STS assume-role calls. Rewrote to: (1) `list_resources_for_services` (plural) in `iam_utils` creates one session and lists all services in a loop, (2) the view calls `_fetch_available_resources` and passes the result through `_group_statements_by_service` into each group's template context, (3) `<option>` elements are rendered server-side in `_permission_service_group.html`. Deleted the `/resources/` JSON endpoint, all JS resource fetching code (`fetchAllResources`, `populateResourcePicker`, `resourceCache`), and the URL route. N assume-role calls → 1.

**Removed dead `security_permissions_editor_statements` endpoint.** Was meant for left-panel polling but was never wired up in any template. Deleted view, URL, and imports.

**Key points:**
- `list_resources_for_service` (singular) became private `_list_resources_for_service` accepting a `session` param; new public `list_resources_for_services` (plural) creates one session and loops
- `_build_service_group_data` adds `available_resources` with `selected` flags so the template can mark already-chosen resources as `disabled` in the dropdown
- No `=None` default parameters — all parameters are required, callers pass explicit empty values
- The HTMX app shell early-return pattern was applied to 9 views across the codebase, and `AGENTS.md` was updated with the correct pattern to prevent the mistake from recurring

## 2026-02-18 20:45 - [UI] HTMX app shell early-return optimization across all views

**Conversation:** [2026-02-18-2000-c8255962.md](conversations/2026-02-18-2000-c8255962.md)

Fixed 9 views that were doing expensive work (DB queries, subqueries, external API calls) on non-HTMX requests where only the app shell frame is needed. The app shell pattern means every full page refresh hits the view twice — once for the shell, once for the content via HTMX. Without the early return, all that work runs twice but is only used once.

The fix is mechanical: add `if not request.htmx: return` before any expensive work. This was applied to `security`, `chat_list`, `chat_view`, `workspaces`, `workspace_detail`, `app_detail`, `dashboard`, `settings_aws_accounts`, and `settings_git_integrations`. The worst offender was `settings_git_integrations` which was calling `github_client.sync_repositories()` (an external GitHub API call) on every full page load.

Also updated `views/AGENTS.md` to show the correct early-return pattern in both the top-level page and nested page examples, with an explicit callout explaining why. The previous examples used `if request.htmx: ... else: ...` which naturally led to hoisting shared setup above the check — and the expensive stuff came along for the ride. The new `if not request.htmx: return` guard clause makes the mistake structurally impossible.

Claude did a mass find-and-replace of the same three-line pattern across 9 views without introducing a single bug. Mass refactoring across view files without breaking anything is not nothing — but let's be honest, the pattern was identified by a human noticing a debugger breakpoint firing twice. The AI just did the mechanical part.

**Key points:**
- The root cause was a misleading example in AGENTS.md — the code template taught the wrong pattern, and every view copied it
- `chat_view` had a subtlety: it checks `request.htmx.target == "chat-panel"` for partial panel updates vs full navigation, so the early return had to come before both checks
- `settings_git_integrations` POST handling (GitHub sync) is safe behind the `not request.htmx` guard because POST requests in this SPA always come via HTMX

## 2026-02-18 20:15 - [DomainModel] Permissions service extraction + dedicated IAM policy

**Conversation:** [2026-02-18-1928-c8255962.md](conversations/2026-02-18-1928-c8255962.md)

Three related changes to the permissions editor, all motivated by separating concerns:

**1. Dedicated `doh-app-permissions` IAM policy.** The editor previously read ALL inline policies from the ECS task role via `list_role_policies`, which surfaced CDK-generated infrastructure plumbing (`TaskRoleDefaultPolicy` with `ssmmessages`, `logs`, `secretsmanager`) that users shouldn't see or modify. Replaced with a single `get_role_policy(PolicyName="doh-app-permissions")` call. `NoSuchEntityException` returns `[]`, so the editor starts blank for new apps or apps that only have CDK policies — this is the desired behavior.

**2. Extracted `services/permissions.py`.** Business logic was living directly in the view (`security.py`). Moved three operations into a new service: `get_or_create_draft()` (PermissionRequest lifecycle + IAM read), `update_statements()` (add/remove service, level, resource mutations), and `approve()` (status flip to APPROVED_PENDING_APPLY). The `DOH_APP_PERMISSIONS_POLICY_NAME` constant lives here too. Views are now thin HTTP handlers: parse request → call service → build template context → render. The service is where we'll later add the `put_role_policy` write-back, callable from a permission worker without any HTTP dependency.

**3. Conversation creation moved to the view.** `get_or_create_draft` was creating a `Conversation` via `agent_service` — mixing permissions domain logic with agent/UI concerns. The conversation is only needed for the chat panel in the editor, so it's now lazily created in `security_permissions_editor` when `permission_request.conversation is None`. The `conversation` FK on `PermissionRequest` was already nullable, so no migration needed.

**4. HTMX app shell early-return optimization.** Discovered that `security_permissions_editor` was doing all its expensive work (IAM calls, DB queries, conversation creation) on EVERY request, including full page loads where only the app shell frame is returned. Added an early return for non-HTMX requests before any expensive work. Then audited all views and found 9 others with the same wasteful pattern — those are queued for a follow-up fix.

**Key points:**
- `read_task_role_statements` renamed to `read_app_permissions_policy` with an explicit `policy_name` parameter — the constant is hardcoded at the service layer, not buried in iam_utils
- The app shell HTMX pattern means every full page refresh hits the view twice (once for shell, once for content). Doing expensive work before the `request.htmx` check means it runs on BOTH hits but is only used by the second
- `settings_git_integrations` is the worst offender in the audit — it calls `github_client.sync_repositories()` (external API) before the HTMX check

## 2026-02-18 18:20 - [AgentChat] Remove agent permissions update capability

**Conversation:** [2026-02-18-1820-107c2e52.md](conversations/2026-02-18-1820-107c2e52.md)

Removed the `update_permission_statements` MCP tool and all supporting SSE OOB plumbing that pushed agent-driven statement changes to the permissions editor UI in real time. The permissions editor works well with manual user edits only, and the agent-write path added complexity (OOB rendering, async template calls, htmx re-processing) that wasn't paying for itself yet. Can be re-implemented later if needed.

The PERMISSIONS conversation mode stays — the chat panel in the permissions editor still works for the agent to discuss permissions, it just can't modify statements directly anymore.

**Key points:**
- Removed the MCP tool definition, its entries in `TOOL_DISPLAY_NAMES`, `TOOL_MAIN_PARAMS`, the tools list, and `TOOL_NAMES`
- Removed `_render_permission_statements_oob` async function from `chat.py` and all `statements_oob_html` handling in the SSE event pipeline (`event_generator`, `_render_streaming_tool_result`, `_format_sse_event`)
- Reverted `processOobElements` in `_chat_panel.html` — removed `htmx.process()` and `htmx.trigger('htmx:afterSwap')` calls that were only needed for the permissions OOB swap; existing OOB swaps (title, cost) are plain text and don't need htmx re-initialization
- `PermissionRequest` import removed from `mcp_tools.py` since it was only used by the deleted tool

## 2026-02-17 19:45 - [AgentChat] Permissions Editor — Two-Panel UI with Agent Chat

**Conversation:**

Replaced the mockup security permissions UI with a real permissions editor backed by the `PermissionRequest` model. The editor is a two-panel layout: left panel shows editable IAM policy statement cards, right panel embeds the existing chat infrastructure for agent-assisted editing.

**Architecture decisions:**

The `PermissionRequest` model is the persistence layer for editing sessions. Each request links to an App, Environment, and Conversation. The `statements` JSONField stores a list of policy statement dicts in a normalized format (`{sid, service, effect, actions, resources}`). Status transitions follow a simple flow: DRAFT → APPROVED_PENDING_APPLY (user clicks Apply) → APPLYING → APPLIED/FAILED (handled by a separate apply engine, out of scope).

When a user navigates to the editor for an app+environment, we either resume an existing DRAFT request or create a new one. On first creation, `read_task_role_statements()` reads the actual inline IAM policies from the task role in the customer's AWS account (via `list_role_policies` + `get_role_policy`), so the editor starts with real data. The task role naming convention (`doh-{env.slug}-{app.slug}-task-role`[:64]) matches the CDK code in `deploy_app.py`.

**Chat integration:** Each PermissionRequest gets a linked Conversation with mode=PERMISSIONS and a trigger message. The right panel reuses `_chat_panel.html` directly — no new chat infrastructure needed. The agent has an `update_permission_statements` MCP tool that can modify the PermissionRequest's statements, and the left panel polls every 3s via HTMX to pick up agent-driven changes.

**Cleanup:** Removed the old mockup pages (permission_request_detail, permission_request_history, permission_request_workspace template, security_task_role_view) and their helper functions. The security hub now shows real PermissionRequest rows with color-coded status badges instead of fake data derived from deployment logs.

**Key points:**
- The `_get_or_create_permission_request` pattern ensures revisiting the same app+environment resumes the existing DRAFT rather than creating duplicates
- AWS IAM reading is done synchronously in the view on first creation only — subsequent visits reuse the stored statements
- The Apply button collects statement data from the DOM via JS (not a form POST) and sends JSON to the apply endpoint, which just flips the status — the actual IAM modification is a separate engine
- Added `_get_aws_session_for_environment()` helper to `iam_utils.py` to centralize the session-creation pattern already used in `app_deployment_executor.py`
- The `Conversation.Mode` field's max_length=20 already accommodates "permissions" (11 chars)

## 2026-02-17 - [UI] Deployment row status widget polish

**Conversation:**

Improved visual design of the deployment status indicator used in `_app_deployment_row.html` and the "Deployed to Environments" section of `app_detail.html`.

**Key points:**
- Grouped the status badge and "time ago" text into a single centered box with rounded border (`border rounded-lg px-3 py-2 text-center`), replacing the flat inline layout where status and time were side-by-side.
- Added `cursor-pointer` to the Redeploy button in `app_detail.html` since Tailwind's preflight resets the cursor on `<button>` elements — without it the pointer hand never appears.
- Fixed vertical centering of the right-side controls (status box + 3-dots menu): the service URL `<p>` was outside the `flex items-center justify-between` container, making the flex height shorter than the visual row. Moving the URL inside the left column ensures both sides share the same height and `items-center` centers correctly.
- The 3-dots menu button is always rendered (never conditionally removed) but marked `invisible` on non-deployed rows — this reserves the fixed column width so the status box stays horizontally aligned across all rows regardless of deployment state.

## 2026-02-14 00:10 - [Deployment] Rename create_environment to provision_environment + retry semantics + ROLLBACK_COMPLETE cleanup

**Conversation:** [2026-02-13-1745-174045a7.md](conversations/2026-02-13-1745-174045a7.md)

When an environment provisioning fails, the agent had no way to recover — calling `create_environment` again raised a ValueError telling the agent to "delete it and try again," but no `delete_environment` tool existed. This was a dead end.

**Retry semantics for provision_environment:** Changed the ERROR branch in the tool to reset the environment record to PENDING instead of raising. This mirrors how `deploy_app` works — each call creates a new deployment attempt. Now calling `provision_environment` on a failed environment resets it to PENDING and the job worker retries. The tool description and all error messages in `deploy_app` and `app_deployment_executor` were updated to guide the agent toward the correct recovery path.

**Renamed create_environment → provision_environment:** The old name implied a one-shot operation that shouldn't be called twice, which conflicted with retry semantics. "Provision" better describes the intent (ensure infrastructure is ready), matches the executor name (`environment_provisioning_executor.py`), and is consistent with `deploy_app` (named after the action, not the side effect of record creation). Renamed across all code, tool definitions, system prompts, and documentation. Historical records (journal, conversation logs) were left unchanged.

**ROLLBACK_COMPLETE stack cleanup:** Testing the retry on localhost revealed a second issue: the VPC CloudFormation stack from the first failed attempt was in `ROLLBACK_COMPLETE` state. `stack_exists()` returned True for it (since `describe_stacks` returns stacks in any state), but it had no outputs, so `get_or_create_vpc_cidr` couldn't read the CIDR and failed. The fix was adding `cleanup_rollback_complete_stacks()` — a self-contained function in `deploy_base.py` that checks all three environment stacks (VPC, cluster, builder) for `ROLLBACK_COMPLETE` status and deletes them before deployment begins. This runs at the top of `deploy()`, making it idempotent regardless of what state previous attempts left behind. Also added `get_stack_status()` to `cloudformation_utils` to check stack status directly rather than relying on indirect signals like missing outputs.

**Key points:**
- Retry pattern: ERROR → PENDING transition only happens when agent explicitly calls `provision_environment` again, not automatically
- The cleanup logic lives in `deploy_base.deploy()` (infrastructure layer), not in the executor — the executor shouldn't know about CloudFormation internals like ROLLBACK_COMPLETE
- `get_or_create_vpc_cidr` stays simple — it just reads or finds a CIDR. Stack cleanup is a separate concern handled before it runs

## 2026-02-13 14:59 - [AgentChat] Strip details from GetEnvironmentStatus MCP tool logs

**Conversation:**

Applied the same context-reduction pattern from `GetDeploymentStatus` (commit `d978aac8f`) to the `GetEnvironmentStatus` MCP tool. Removed the `details` field from the `EnvironmentLogEntry` dataclass and its constructor call in `get_environment_status.py`.

The `details` dict contains `template`, `params`, `logger`, and `stream` — all redundant because the `message` field already has the fully rendered text. Every environment status check was sending ~20 lines of JSON per log entry to the LLM, most of it noise. With `details` removed, each log entry shrinks from ~20 lines to ~4 lines, meaningfully reducing token usage per status call.

This is a read-path-only change — `EnvironmentLog` still stores the full `details` in the database for debugging. Only the MCP tool's response to the agent was trimmed.

**Key points:**
- Exact same pattern as `d978aac8f` for `DeploymentLogEntry` — consistency across both status tools
- `message` already contains rendered text, making `details.template` and `details.params` redundant for LLM consumption
- `details.logger` and `details.stream` are internal metadata not useful for agent decision-making

## 2026-02-13 12:30 - [Deployment] Pulumi research session — policy enforcement, IAM permissions, and deployment pipeline architecture

**Conversation:** [2026-02-13-1025-174045a7.md](conversations/2026-02-13-1025-174045a7.md)

Research session exploring Pulumi concepts and how they map to DOH's architecture, with an eye toward the internal vibe-coded apps use case.

**Generated CDK code in the customer's repo:** Instead of DOH executing parameterized CDK internally, the agent would generate CDK code + a config file, commit it to a branch in the customer's repo, and open a PR. This makes infrastructure visible, auditable, and reproducible. The customer's repo becomes the source of truth. `cdk diff` gives preview/approval for free.

**Staged deployment pipeline:** Moving from "agent does everything in one shot" to defined stages with clear inputs/outputs: Analyze → Generate → Validate → Preview → Execute → Verify. Each stage produces a concrete artifact. The user has natural approval points after Generate and Preview. Error recovery re-enters the pipeline at the appropriate stage rather than restarting from scratch. An agent handles the judgment calls within and between stages, but the stages provide structure and visibility.

**Policy enforcement (documented in `docs/policy_enforcement_analysis.md`):** Pulumi CrossGuard has three layers: Policy (single rule), Policy Pack (bundle of rules), Policy Group (central assignment to stacks). CDK/AWS has equivalents for the first two (cdk-nag Aspects, CloudFormation Guard) but lacks the management layer (Policy Groups). DOH would need a domain model addition — PolicyPack entity with assignment to Environment. Since DOH controls the full pipeline, client-side validation (Guard rules against synthesized CloudFormation templates) is sufficient; server-side enforcement (CloudFormation Hooks) is unnecessary. For internal apps, the relevant policy packs are cost guardrails, internal-network-only, auto-cleanup/TTL, and security basics — not regulatory compliance.

**Pulumi's audit policy groups:** Different from advisory/mandatory enforcement. Pulumi's audit mode runs continuous scans against live infrastructure in the AWS account, including resources not managed by Pulumi (manual changes, Terraform, CloudFormation). This is a cloud security posture tool, not a deployment guardrail. DOH already has the IAM cross-account role to do this, but it's a different product surface — noted as a future expansion path, not near-term scope.

**IAM permissions as a product feature:** Inspired by ConsoleMe (Netflix) and Noq.dev (Curtis Castrapel's startup that failed because it sold to DevOps teams instead of the developers who actually feel the pain). DOH's approach: the agent deploys with minimal permissions on first iteration. A specialized permission agent (separate from the deployment agent) analyzes source code, ECS task logs, and optionally CloudTrail to propose permission changes. These go through an approval workflow — approver sees the IAM policy diff in the UI, approves or rejects asynchronously, DOH applies the stored change upon approval. The agent's job ends at proposal; the rest is mechanical.

**IAM permission UI:** Hybrid approach — agent generates the proposal, UI renders it as an editable structured form (service dropdown, action multi-select, resource ARN field). The agent fills it in for non-DevOps users; power users can modify directly before approving. Avoids forcing either audience into the wrong interaction model.

**IAM policy structure:** One inline policy per AWS service on the task role. Clean to reason about, maps well to the UI (each service is a card), and makes approval diffs easy to review — "App X is requesting S3 read access" is one isolated policy.

**Prioritization insight:** Approval workflows before Guard rules. DevOps leads and managers want to see and approve permissions themselves before trusting automated policy checks. Guard rules reduce the approver's workload once trust in the system exists. They're complementary, but approval comes first.

**Key points:**
- TypeScript vs Python for generated CDK: doesn't matter if the agent generates the code — the language is part of the prompt/output, not a maintenance burden
- `env_slug` already functions as DOH's equivalent of Pulumi's `getStack()` — just threaded explicitly rather than pulled from ambient context
- Per-environment config (stack concept gap) is independent from policy groups — they assign different things to environments
- Curtis Castrapel's lesson: don't sell IAM governance to DevOps teams (vitamins); sell it to the developers who feel the pain (painkillers)
- TTL stacks and cost-focused policy enforcement are the highest-value Pulumi enterprise features for the internal apps use case

## 2026-02-12 12:15 - [Deployment] Replace `_populate_service_urls()` with `DeployResult` from `deploy()`

**Conversation:** [2026-02-12-1951-ab7eb6a4.md](conversations/2026-02-12-1951-ab7eb6a4.md)

Refactored how deployment URLs (HTTPS URL and ALB DNS) flow from CloudFormation back to the caller. Previously, after `deploy()` returned a boolean, the executor made a separate CloudFormation `describe_stacks` API call via `_populate_service_urls()` to read back the `HttpsUrl` and `SharedAlbDns` stack outputs. Meanwhile, `print_deployment_summary()` (called inside `deploy()`) did the exact same fetch via `get_app_urls()`. This meant two redundant CloudFormation API calls for the same data.

The fix introduces a `DeployResult` dataclass returned from `deploy()` that carries `success`, `error`, `service_url`, and `alb_dns`. After successful CDK deployment, `deploy()` calls `get_app_urls()` once using its existing `cf_client`, passes the URLs to `print_deployment_summary()` (which no longer fetches them itself), and returns them in the result. The executor reads URLs directly from the result object, eliminating `_populate_service_urls()` entirely.

The `error` field was added so that specific failure reasons (e.g. "CDK deployment failed", "Docker build/push failed", "Failed to start ECS service") propagate to `deployment.status_message` instead of a generic hardcoded string.

**Key points:**
- `DeployResult` is a `@dataclass` with four fields: `success: bool`, `error: str`, `service_url: str`, `alb_dns: str`
- `service_url` is the best available URL — HTTPS if a domain is configured, otherwise the HTTP ALB URL
- `alb_dns` is the raw ALB DNS hostname (without `http://` prefix), matching what the `Deployment` model stores
- `print_deployment_summary()` signature simplified: dropped `cf_client`, `has_domain`, `env_slug`, and `cluster_name` (the latter was already unused); now accepts `service_url` and `alb_dns` directly
- `doh_raw.py` (CLI command) appends `.success` to the `deploy()` call since it only needs the boolean
- Each failure path in `deploy()` has a specific error message rather than sharing a single `fail` object

## 2026-02-11 23:50 - [AgentChat] Fix ~5s SSE stream delay and spurious reconnection when switching conversations

**Conversation:** [2026-02-11-2205-614f1627.md](conversations/2026-02-11-2205-614f1627.md)

Investigated a performance issue where clicking a different conversation in the chat sidebar caused the `stream/` SSE endpoint to stay pending for ~5 seconds, then close, and then a second `stream/` request would fire (taking 1.8 minutes). The UI itself refreshed immediately — the problem was entirely in the SSE lifecycle.

**Root cause:** Three interacting issues in the agent runner and SSE plumbing:

1. **Eager `MainAgent.create()` on every conversation visit.** In `_run_agent_loop`, the runner called `MainAgent.create(conversation)` immediately at startup — before checking if there was a pending user message. This triggers system prompt building, optional git clone, Claude SDK client instantiation, and `client.connect()` (session resumption) on every navigation, even when just viewing an idle conversation. This took ~5 seconds and was completely wasted when there was no message to process. If the initialization failed (API timeout, etc.), the runner crashed, sending an error + `None` sentinel to the event queue.

2. **No `sse-close` event to prevent browser auto-reconnection.** The browser's `EventSource` API has built-in automatic reconnection. When the first `stream/` closed (due to the runner crashing from a failed `MainAgent.create()`), the browser immediately opened a second connection. The HTMX SSE extension v2.2.4 supports an `sse-close` attribute to gracefully close the EventSource, but we weren't using it.

3. **The second connection stayed open indefinitely.** If the second `MainAgent.create()` succeeded (or the conversation had no repo to clone), the runner entered an idle polling loop (0.5s sleep intervals) and the SSE connection stayed open with 15s keepalives for as long as the user stayed on the page — explaining the 1.8 minute duration.

**Fix (two changes):**

1. **Lazy agent initialization** — Moved `MainAgent.create()` from the top of `_run_agent_loop` (unconditional) to inside the `if pending_message is not None` branch. Now the expensive SDK connection only happens when there's actually a user message to process. Viewing an idle conversation costs nothing beyond a lightweight DB poll every 0.5s.

2. **Added `sse-close` SSE event** — When the event_generator receives the `None` sentinel (runner finished), it now emits an `sse-close` event before breaking. Added `sse-close="sse-close"` to the `#messages` div in `_chat_panel.html` so the HTMX SSE extension calls `EventSource.close()` instead of allowing automatic reconnection.

Initially also proposed a `task.done()` guard in `ensure_agent_running` to detect stale runners mid-shutdown, but removed it after review — with lazy init, the shutdown race window shrinks to microseconds for idle conversations, making the guard unnecessary complexity.

**Key points:**
- The HTMX SSE extension uses the standard `EventSource` API which auto-reconnects by default when a connection closes. The `sse-close` attribute (added in htmx-ext-sse 2.x) sends a close signal that prevents this.
- `MainAgent.create()` involves: `_build_system_prompt` (DB), `_detect_and_prepare_fork` (DB/IO), optional `clone_repository` (git, but skipped if dir exists), `ClaudeSDKClient` + `client.connect()` (API connection + session resume). The `connect()` call is likely the expensive part (~5s).
- The `agent.shutdown()` method drains remaining messages with a 10-second timeout (`async with asyncio.timeout(10)`), which creates a window where a dying runner still exists in `_runners` — but this only matters if the agent was actually initialized (which lazy init avoids for idle views).
- `clone_repository` already had a guard (`if target_dir.exists(): return`) so re-cloning wasn't the bottleneck — the SDK connection was.

## 2026-02-10 23:15 - [Bugfix] Fix psycopg3 connection pool exhaustion causing site hangs during deployments

**Conversation:** [2026-02-10-2341-5b9f138d.md](conversations/2026-02-10-2341-5b9f138d.md)

Investigated a production incident where the site became completely unresponsive (endpoints not replying at all) while a deployment was in progress. The user was in the chat window watching a deployment complete, then clicked to navigate to another page — the loading spinner spun indefinitely and the network tab showed the request never received a response.

**Root cause:** psycopg3's `ConnectionPool` with `pool=True` defaults to a fixed pool of 4 connections (`min_size=4, max_size=None`, and the docs explicitly state that `None` means "equal to `min_size` — the pool will not grow or shrink"). The job worker runs in the same Uvicorn process as the web server and spawns background threads for deployments, provisioning, and teardowns. None of these thread functions called `connections.close_all()`, so each thread held a database connection from the pool for its entire lifetime — a deployment thread holds one for 5+ minutes. With only 4 connections in the pool and 2+ held by job worker threads, the remaining connections became contended. When timing aligned poorly (SSE disconnect returning the main thread's connection to the pool, followed by the agent runner re-acquiring it before the sync view could), the main `sync_to_async(thread_sensitive=True)` thread would block waiting for a pool connection — and since ALL sync views in ASGI mode run on that single thread, the entire site hung.

**Fix (two parts):**

1. Added `connections.close_all()` in `finally` blocks to all 4 job thread target functions (`_run_app_deployment_thread`, `_run_environment_provisioning_thread`, `_run_app_deployment_teardown_thread`, `_run_environment_teardown_thread`) and to the worker polling loop. This matches the pattern already used in `chat.py` and `agent_runner.py` for async contexts.

2. Changed the pool from `pool=True` (fixed at 4) to `pool={"min_size": 4, "max_size": 200}`. This allows the pool to grow under load while keeping a small idle baseline. Idle connections above `min_size` are reaped after 10 minutes (`max_idle` default). Aurora Serverless v2 supports ~1000 connections, so 200 is conservative.

**Key points:**
- psycopg_pool 3.3.0 docs: `max_size=None` means "equal to `min_size`" — the pool is fixed, not unbounded. This is a common misconception.
- Django's `request_finished` signal (which normally returns connections) doesn't fire for background threads or SSE generators — manual `connections.close_all()` is required.
- The `thread_sensitive=True` single-thread model in ASGI means ANY blocking operation on the main thread (like waiting for a pool connection) blocks ALL sync views.
- CloudWatch Aurora metrics showed steady 4 connections, confirming the pool was fixed at 4. The `pg_stat_activity` count of 15 seen in SSE cleanup logs reflected psycopg3's pool overhead, not actual leaked connections.
- Diagnosis involved checking ECS service status, ALB target health, CloudWatch CPU/memory metrics, Aurora connection metrics, deployment logs, and conversation status — production was healthy but the pool configuration was a time bomb.

## 2026-02-10 22:51 - [UI] Unify deployment row template across app and environment detail views

**Conversation:** [2026-02-10-2252-1968ac6c.md](conversations/2026-02-10-2252-1968ac6c.md)

The app detail and environment detail views both had "Recent Deployments" sections, but they used completely different HTML for the deployment rows. The app detail view used a shared partial (`_app_deployment_row.html`) with full features (status spinner, teardown menu, HTMX polling, clickable service URL), while the environment detail view had simpler inline HTML missing most of those features.

Unified both views to use a single `_app_deployment_row.html` template controlled by a `mode` variable passed via `{% include ... with mode="environment" %}`. The default mode renders for the app detail context (shows environment name, AWS account/region, git ref), while `mode="environment"` renders for the environment context (shows app name, workspace name, git ref). All shared features — status badges with tearing-down spinner, teardown menu, HTMX polling, clickable service URLs — are now available in both views.

The HTMX polling URL needed special handling: the `mode` query parameter is forwarded via `?mode={{ mode }}` so that when `app_deployment_status` re-renders the row, it preserves the correct layout. The view was updated to read `mode` from `request.GET` and pass it to the template context.

Also made the entity names (environment, app, workspace) clickable links navigating to their respective detail pages using the standard HTMX SPA pattern, styled as visible indigo links rather than plain text.

**Key points:**
- Used a single `mode` variable instead of multiple boolean flags — cleaner and more extensible if new contexts are needed
- Changed URL tag references from `app.slug` to `deployment.app.slug` so the template is self-contained and doesn't depend on an `app` variable in the parent context
- The `mode` round-trips through HTMX polling via query parameter to prevent layout switching on re-render
- Environment detail view now gains teardown menu and polling it was previously missing

## 2026-02-10 19:40 - [AgentChat] Strip verbose details from MCP deployment status logs

**Conversation:** [2026-02-10-1940-f40c6a2b.md](conversations/2026-02-10-1940-f40c6a2b.md)

The `get_deployment_status` MCP tool was returning the full `details` JSON field from each `DeploymentLog` entry. This field contains `template`, `params`, `logger`, and `stream` — all redundant since the `message` field already has the rendered text. Every status check was sending ~20 lines of JSON per log entry to the LLM, most of it noise that inflates context unnecessarily.

Removed the `details` field from `DeploymentLogEntry` in `get_deployment_status.py`. Each log entry now only includes `source`, `level`, `message`, and `created_at` — cutting per-entry size from ~20 lines to ~4 lines. With 10 logs per status check, this is a meaningful reduction in token usage for every deployment monitoring call.

**Key points:**
- The `message` field already contains the fully rendered log text, making `details.template` and `details.params` redundant for the LLM consumer
- `details.logger` and `details.stream` are internal implementation metadata not useful for the agent's decision-making
- This is a read-path-only change — the `DeploymentLog` model still stores the full `details` in the database for our own debugging needs

## 2026-02-10 20:48 - [Deployment] Populate service_url from CloudFormation and show on app cards

**Conversation:** [2026-02-10-1940-1968ac6c.md](conversations/2026-02-10-1940-1968ac6c.md)

Wired up the `service_url` and `alb_dns` fields on the Deployment model, which existed but were never populated. After a successful deploy in `app_deployment_executor.py`, we now call `cloudformation_utils.get_app_urls()` to extract the URLs from CloudFormation stack outputs. The best URL is chosen as `service_url` (preferring `https_url` over `alb_url`), and the raw ALB DNS is stored in `alb_dns` by stripping the `http://` prefix from `alb_url`. The logic is in a `_populate_service_urls` helper, wrapped in try/except so a failure to fetch URLs doesn't break the deployment itself.

**Unified app card partial:** The dashboard and workspace detail had divergent app card implementations — dashboard used an overlay `<a>` pattern (allowing nested clickable links), while workspace wrapped the entire card in a single `<a>` tag with different fields (Environments instead of Last deployed/Status). Extracted a shared `partials/_app_card.html` partial that both pages now include. The partial accepts a `show_workspace` variable to conditionally render the Workspace row (shown on dashboard, hidden on workspace detail since it's redundant). Added the same `last_deployed_at`, `latest_status`, and `running_service_url` annotations to the workspace view's queryset to match the dashboard.

**URL display:** The URL link shows the app name as clickable text rather than the full URL, with the full URL revealed on hover via `title` attribute. This keeps the card clean while still providing the full URL when needed.

**Key points:**
- `_populate_service_urls` only runs at deploy time — existing deployments need a redeploy to get URLs populated
- Dashboard URL is a clickable `<a>` with `relative z-10` to sit above the card's full-area overlay link
- The workspace card switched from the `<a>`-wrapper pattern to the dashboard's overlay pattern, gaining nested clickable links
- Diagnosed "URL not showing" issue: the job worker needed a restart to pick up the new code

---

## 2026-02-10 17:25 - [UI] App teardown exposed in web UI with HTMX polling and idiomorph

**Conversation:** [2026-02-10-1726-9aa277e5.md](conversations/2026-02-10-1726-9aa277e5.md)

Exposed the existing CLI-only app teardown (`doh_control teardown-app`) in the web UI. Users can now tear down deployments directly from the app detail page without SSH/CLI access. The deployment row shows a kebab (3-dot) menu for deployments in `running` or `failed` status, which opens a confirmation modal, then polls for status updates until the deployment is deleted.

**Architecture — partial extraction + HTMX polling:** Extracted the deployment row from `app_detail.html` into `_app_deployment_row.html` so it can be rendered both inline (full page) and standalone (returned by teardown/status endpoints). The row self-polls every 3s via `hx-get` with `hx-trigger="load delay:3s"` when in `teardown_pending` or `tearing_down` status. When the deployment record is deleted (teardown succeeded), the status endpoint returns empty HTML, and `hx-swap="morph:outerHTML"` replaces the row with nothing, removing it from the page.

**CSRF gotcha with HTMX partials:** Initially added `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` on the confirm button inside the modal. This broke with "CSRF token has incorrect length" because the modal is loaded via an HTMX GET request and `{{ csrf_token }}` renders empty in that context. The fix: remove it entirely — CSRF is already configured globally on `<body>` in `base.html` with `hx-headers='{"x-csrftoken": "{{ csrf_token }}"}'`, and all HTMX requests inherit it. Documented this in `views/AGENTS.md`.

**Modal close timing with hx-post:** The confirm "Tear Down" button uses `hx-post` targeting the deployment row. Initially used `onclick` to close the modal, but that removes the button from the DOM before HTMX fires the request. Then tried `hx-on::before-request`, but HTMX aborts if the element leaves the DOM after that event. The working solution: `hx-on::after-request` — the POST completes, the row is swapped, then the modal is cleared.

**Idiomorph for animation continuity:** The `tearing_down` status shows a CSS spinner (`animate-spin`). With plain `outerHTML` swap, the spinner restarts its animation every 3s poll. Added the idiomorph extension (`idiomorph-htmx.min.js`) and switched to `hx-swap="morph:outerHTML"` — idiomorph diffs the DOM and leaves unchanged elements in place, preserving the spinner animation across polls.

**Deployment IDs are UUIDs, not ints:** The initial URL patterns used `<int:deployment_id>`, which caused `NoReverseMatch` because the Deployment model uses UUID primary keys. Fixed to `<uuid:deployment_id>`.

**Key points:**
- Teardownable statuses: only `RUNNING` and `FAILED` — matches the CLI command's validation logic
- Status endpoint uses `try/except Deployment.DoesNotExist` instead of `get_object_or_404` so it can return empty HTML (row removal) instead of 404
- Kebab menu uses `data-menu` / `data-menu-items` attributes with a global click-outside listener for closing
- Three new URL patterns: `teardown/` (POST), `status/` (GET polling), `teardown-confirm/` (GET modal)

---

## 2026-02-09 22:41 - [AgentChat] Show error icon when MCP tools report business-logic failure

**Conversation:** [2026-02-09-2241-c553c4cf.md](conversations/2026-02-09-2241-c553c4cf.md)

When an MCP tool like "Test Docker Build" fails, the UI was showing a green success checkmark even though the custom template correctly showed "Build Failed" in red. The header icon and the result badge used different data sources that disagreed.

**Root cause:** The MCP protocol has two layers of error reporting. The transport-level `isError` flag (set when a tool throws an exception) controls `block.is_error` in the SDK, which our `_handle_tool_results` maps to `status = "error"`. But tools that succeed at the transport level while reporting domain failure through their response data (e.g., `{"success": false, "build_output": "..."}`) always got `status = "success"`. The SDK's `@tool` decorator even documents an `"is_error": True` return field, but the SDK's internal `call_tool` function strips it and only returns content — a bug in `claude-agent-sdk`.

**Decision — UI-level fix, don't patch the SDK:** Rather than modifying the vendored SDK (fragile across updates) or making tools throw exceptions (loses structured result data), we override status at the UI layer by checking the parsed result's `success` field.

**Simplification:** Also consolidated the template context — removed `result_json` (pre-formatted string) and `custom_result_template_data` (conditionally-passed dict) in favor of a single `tool_result` variable. The generic template path uses `{{ tool_result|json_pretty }}` (filter already existed), and custom templates use dict access (`tool_result.success`). Both the streaming and persisted-message rendering paths now share the same variable name convention.

**Key points:**
- MCP protocol supports `isError: true` for business-logic failures, but the `claude-agent-sdk`'s `call_tool` at line 303 discards the field — only forwarding content, not `is_error`
- If a tool throws an exception, `isError` propagates correctly through the MCP SDK's lowlevel server, but you lose structured result data (build output, etc.)
- The status override uses `result_parsed.get("success") is False` — `None is False` is `False`, so tools without a `success` field are unaffected
- Two rendering paths exist: streaming (view computes status in Python) and persisted (template filter `tool_effective_status` checks both `metadata.status` and inner `success` field)

---

## 2026-02-09 19:05 - [Deployment] ALB health checks fail when apps enforce HTTPS redirects (force_ssl)

**Conversation:** [2026-02-09-1859-c4ef767f.md](conversations/2026-02-09-1859-c4ef767f.md)

Debugged a Phoenix app (ai-detector-and-humanizer) whose ECS task kept failing ALB health checks with `Task failed ELB health checks`. The ECS task logs showed: `Plug.SSL is redirecting GET /health to https://... with status 301`.

**Root cause — the full chain:**

The ALB terminates SSL and forwards all traffic to containers over plain HTTP. For regular user requests, the ALB adds `X-Forwarded-Proto: https` to signal "this originally came over HTTPS." Frameworks like Phoenix (`force_ssl: [rewrite_on: [:x_forwarded_proto]]`) and Rails (`config.force_ssl`) check this header — if it says `https`, they pass the request through; if it says `http` or is missing, they 301 redirect to HTTPS.

ALB health checks are synthetic HTTP requests generated internally by the load balancer. They don't include `X-Forwarded-Proto`, and AWS provides no way to add custom headers to them. So apps with `force_ssl` see the health check as a plain HTTP request and redirect it. Our ALB target group was configured with `healthy_http_codes="200"`, so the 301 was treated as unhealthy.

The DOH agent created the `/health` endpoint correctly in the Phoenix router, but the `force_ssl` middleware in `config/prod.exs` intercepts requests at the Endpoint level *before* they reach the Router — so the health route was never hit. The agent had no awareness of this because the system prompt contains no guidance about framework-level SSL enforcement.

**Fix — accept 301 as healthy:**

Changed `healthy_http_codes` from `"200"` to `"200,301"` in the ALB target group health check configuration in `deploy_app.py`. This is an infrastructure-level fix that works universally for any framework that enforces HTTPS, without requiring the agent to detect and modify framework-specific SSL config. A 301 still proves the web server is running and processing requests.

Considered alternatives: teaching the agent framework-specific knowledge (fragile, incomplete), TCP health checks (weaker signal — port can be open while app is stuck). The 301 approach is the right pragmatic default.

**Key points:**
- ALB health checks are always HTTP, and you cannot customize their headers — the fix must be app-side or infra-side
- `force_ssl` with `rewrite_on: [:x_forwarded_proto]` means "trust the proxy header to decide if HTTPS" — but health checks don't have this header
- The original `prod.exs` even had a commented-out `# paths: ["/health"]` exclude, but it was (a) commented out and (b) at the wrong nesting level (sibling of `force_ssl` instead of inside it)
- This pattern affects Phoenix, Rails, Django (`SECURE_SSL_REDIRECT`), and any framework with middleware-level HTTPS enforcement

---

## 2026-02-07 20:55 - [AgentChat] Conversation forking via URL for faster debugging

**Conversation:** [2026-02-07-1328-e7b9f2be.md](conversations/2026-02-07-1328-e7b9f2be.md)

Added conversation forking so you can branch from any completed conversation and continue with the agent remembering the full prior context. The motivation is debugging: when a conversation fails at turn 15, you had to re-run turns 1-14 from scratch (expensive in tokens, time, and non-deterministic). Now you visit `/chat/<conversation-id>/fork/` and get a new conversation where the agent picks up where the source left off.

**Design decisions and trade-offs:**

- **URL-based trigger** — Fork is just a GET to `/chat/<id>/fork/` that creates a new conversation and redirects. No UI changes, no buttons, no banners. This is a developer debugging tool; the person forking knows what conversation they came from.

- **Zero model fields** — Instead of adding `forked_from` or `fork_pending` fields to the Conversation model, we derive the fork signal from existing state. The key insight: normal conversations have `session_id=None` until their first agent turn completes (set by `stream_response` after the SDK returns). A forked conversation has `session_id` copied from the source before any agent turn. So `stream_response` checks: if `session_id is not None` and no agent messages exist, it's a fork. This condition is impossible for normal conversations.

- **Removed `fork_session` parameter from `stream_response`** — Previously `fork_session: bool` was threaded through `stream_response` -> `agent_runner` -> `_create_agent_options`. Now `stream_response` detects forks internally, so `agent_runner` doesn't need to know about forking at all. The `test_main_agent.py` CLI was also simplified — `fork_session` was removed from the entire call chain (`_stream_agent`, `_run_agent_once`, `_run_repl`, `_run`).

- **Session file copy for cwd mismatch** — The Claude CLI indexes session files by working directory at `~/.claude/projects/{cwd-with-slashes-replaced-by-dashes}/{session_id}.jsonl`. Each DOH conversation gets its own sandbox (`sandbox/conv-{id}/src/`), so a forked conversation has a different cwd than the source. The SDK couldn't find the session file because it was looking in the wrong project directory. Fix: `_ensure_session_file_for_fork` searches `~/.claude/projects/` for the session `.jsonl` and copies it to the fork's project directory before the SDK initializes. After forking, the SDK creates a new session file with a fresh UUID in the fork's directory — the copied source file is only needed for initialization.

- **Context fields copied** — The fork view copies `mode`, `context_workspace`, `context_repository`, and `context_aws_account` from the source conversation so the forked conversation has the same system prompt and tool configuration.

**Key points:**
- The Claude CLI's session storage path encoding is: absolute cwd path with every `/` replaced by `-` (e.g., `/Users/foo/bar/` becomes `-Users-foo-bar`)
- The Agent SDK and Claude Code CLI share the same underlying `claude` binary — session storage, resume, and fork are CLI features, not SDK features
- When `fork_session=True`, the SDK reads the source session, creates a brand-new session UUID, and writes a new `.jsonl` file. The source session is not modified.
- The `_resolve_conversation_for_fork` in `test_main_agent.py` was also updated to save `session_id` to the DB (was in-memory only before) and to copy context fields from the source

---

## 2026-02-07 00:00 - [Bugfix] Claude Code SDK session persistence — process killed before session flush

**Conversation:** [2026-02-06-2348-014ddac5.md](conversations/2026-02-06-2348-014ddac5.md)

Deep debugging session to fix a `ProcessError` on the second message turn of conversations without a cloned repository. The error was `"No conversation found with session ID: ..."` — the CLI couldn't resume a session whose data had never been persisted to disk.

**The symptom:** Conversations with a cloned git repo (app deployment mode) worked fine across multiple turns. Conversations without a repo (general mode, empty sandbox directory) failed on every second turn with exit code 1.

**The red herring — git repos:** Initial investigation found that the Claude CLI stores session data in `~/.claude/projects/{cwd-slug}/{session_id}.jsonl`. For working conversations (cloned repo in cwd), the session file contained full conversation data (user/assistant messages, ~27KB). For broken conversations (empty dir), only a 139-byte dequeue marker was written — no conversation data at all, despite the first turn completing successfully with a full response. This led to a long detour trying `git init` and `git init + commit` in the sandbox directory, thinking the CLI required a git repo to persist sessions. None of these worked.

**The actual root cause — process lifecycle:** The SDK's `async with ClaudeSDKClient` pattern calls `connect()` then `query()` to send the user message. With `query()`, stdin stays open (the CLI expects more turns). When the `async with` block exits, `__aexit__` calls `disconnect()` which calls `transport.close()` — this sends SIGTERM to the CLI process. The CLI was being killed before it could flush session data to disk. With repo-context conversations, the timing happened to work (possibly because the CLI persists earlier when there's git context). Without a repo, the CLI hadn't persisted yet when SIGTERM arrived.

**The fix — `connect(prompt=...)` + `receive_messages()`:** Instead of `connect()` + `query()` + `receive_response()`, we now use:

- **`connect(prompt=async_generator)`** — The SDK's `stream_input()` sends the message and then, critically, calls `end_input()` (closes stdin) after the ResultMessage. This signals the CLI that no more input is coming, so it can persist session data and exit cleanly.
- **`receive_messages()`** instead of `receive_response()` — Keeps reading until the CLI actually exits (stream ends), rather than stopping at ResultMessage and immediately tearing down. A try/except around the loop ignores transport errors that occur after the ResultMessage has been received (the CLI exiting with a non-zero code during cleanup is harmless once we have the result).

**Why `receive_messages()` doesn't hang:** With `query()`, the CLI stays alive waiting for more input, so `receive_messages()` would block forever. But with `connect(prompt=...)`, stdin gets closed after the result, so the CLI persists and exits, which terminates the message stream naturally.

**Key points:**
- The SDK has two usage patterns: `connect()` + `query()` for multi-turn within a single connection (stdin stays open), and `connect(prompt=...)` for single-response workflows (stdin closes after result). Our code is single-response per connection (we create a new `ClaudeSDKClient` per turn), so the prompt-based pattern is correct.
- The `stream_input()` method has special handling for MCP servers: it waits for the ResultMessage before closing stdin, ensuring bidirectional MCP communication can complete. Without MCP servers, stdin would close immediately after the prompt is sent.
- The 139-byte dequeue marker was written at CLI startup (before any conversation), not at the end. Session conversation data was supposed to be appended at shutdown — which never happened because SIGTERM killed the process.
- Session files for working conversations had a `"gitBranch": "master"` field in user messages, which was a distraction — it's just metadata the CLI includes when it detects a git branch, not a persistence requirement.
- This was identified with help from Codex, which was given a factual summary of all findings and suggested the `connect(prompt=...)` + `receive_messages()` approach.

---

## 2026-02-06 18:45 - [Deployment] Dockerfile build testing for the generate-dockerfile sub-agent

**Conversation:** [2026-02-06-2237-aeec6f8c.md](conversations/2026-02-06-2237-aeec6f8c.md)

The dockerfile generator sub-agent had no way to verify that the Dockerfile it produced actually builds. A bad Dockerfile (wrong COPY paths, missing system packages, wrong base image) would only be caught later when the real deployment failed — a slow and expensive feedback loop.

Added a `test_docker_build` MCP tool that the sub-agent calls after writing each Dockerfile. The tool runs `docker build` only (no push) and returns success/failure with the build output. Locally it uses the host Docker daemon; in production it delegates to the same EC2 builder machine used for real deployments, reusing the existing SSH-over-SSM infrastructure.

**Architecture — parameterized `run_remote_docker_build`:** The original `run_remote_docker_build` in `ec2_builder_utils.py` did build + ECR login + push. Rather than duplicating 60 lines of SSH setup boilerplate into a `run_remote_docker_build_only` function, we parameterized the existing function with `image_uri: str | None`. When `image_uri` is provided, it does the full build+push flow. When `None`, it builds with a throwaway tag and discards the image. The SSH keygen, Instance Connect key push, and SSM proxy setup are shared. Clean at the call site: `image_uri=image_uri` for deployment, `image_uri=None` for test.

**Architecture — sandbox path extraction:** The sandbox path computation (`CLAUDE_SANDBOX_DIR / conv-{id} / src`) was defined as a private function inside `agent_service.py` (documented as "single source of truth" but not reusable). The new tool needed the same paths. Extracted `SandboxPaths` dataclass and `get_sandbox_paths()` into `agent/sandbox.py` so both `agent_service.py` and the tool import from one place.

**Architecture — tool placement:** The `test_docker_build` tool is an MCP tool registered on the `devopshero` server, included in the sub-agent's `AgentDefinition.tools` list as `mcp__devopshero__test_docker_build`. Sub-agents inherit MCP servers from the parent agent, so this works without any changes to the SDK wiring. The main agent doesn't call this tool directly — it's solely used by the dockerfile generator sub-agent.

**Prompt engineering lesson — sub-agents ignoring failures:** First test run showed the sub-agent calling `test_docker_build`, getting a failure (Docker daemon not running), and then proceeding to report the Dockerfile path as if nothing happened. The original prompt said "if all 3 fail, report the failure" but never explicitly said "do NOT report a path if the build failed." Added a CRITICAL instruction forbidding path reporting on failure, and also distinguished infrastructure errors (Docker not running — don't retry) from Dockerfile errors (wrong package — retry up to 3 times). Sub-agents need very explicit success/failure contracts in their prompts.

**Key points:**
- Build output is truncated to 80 lines before returning to the LLM to avoid flooding the context window
- The tool gets its AWS session the same way the deployment executor does — assumed role via `iam_utils.get_assumed_role_session()`
- The environment slug is passed from the main agent through the sub-agent task prompt — by step 6 (Dockerfile generation) in the deployment flow, the target environment is always known
- Local builds use `--platform linux/arm64` to match the Fargate target, same as production builds

---

## 2026-02-06 11:41 - [AgentChat] Bookmarkable URL shortcut for app deployment conversations

**Conversation:** [2026-02-06-1141-39a89719.md](conversations/2026-02-06-1141-39a89719.md)

Added a human-readable URL endpoint to start app deployment conversations without navigating the UI. The existing `/chat/new/?workspace=<uuid>&repo=<uuid>` endpoint already worked but required remembering UUIDs — unusable as a bookmark.

New endpoint: `/chat/app_deploy/<workspace_slug>/<repo_full_name>/` where `repo_full_name` can be either `owner/repo-name` (2 path segments) or just `repo-name` (1 segment) for repos whose `full_name` has no owner prefix. The URL always represents the repository's `full_name` field, with slashes naturally becoming path separators.

**Design decisions:**

- **`chat/app_deploy` prefix** — Keeps it in the `/chat/` namespace (consistent with existing chat routes) while being descriptive about intent. Alternatives like `/deploy/` were considered but breaking out of the namespace wasn't worth the marginal brevity gain.
- **Workspace slug, not name** — `Workspace` already has a `slug` field (unique per org), making it URL-safe by design. No new fields needed.
- **`full_name` via path segments, not `name` lookup** — Repository uniqueness constraint is on `(organization, full_name)`, not `(organization, name)`. Looking up by `name` alone could be ambiguous if the same repo name exists under different owners. Instead, the URL always encodes the `full_name` and the lookup uses `full_name__iexact`. If someone omits the owner for a repo that has one, it simply 404s — correct behavior since they gave the wrong `full_name`.
- **Two Django URL patterns, one view** — The 3-segment pattern (`workspace/owner/repo`) is registered before the 2-segment one (`workspace/repo`) so Django matches the longer path first. The view receives `repo_owner=None` for the short form and reconstructs `full_name` accordingly.

**Key points:**
- The view delegates to the same `agent_service.create_conversation()` used by `chat_new`, so all existing conversation setup logic (mode auto-derivation, system trigger message creation) is reused
- Case-insensitive lookup on `full_name` keeps URLs forgiving (matches existing pattern in `test_main_agent` harness)
- Example bookmark for test defaults: `/chat/app_deploy/default/vmendi/ai-detector-and-humanizer/`

---

## 2026-02-05 23:21 - [AgentChat] Hide Nixpacks/buildpack from deployment agent

**Conversation:** [2026-02-05-2321-c0bc30f1.md](conversations/2026-02-05-2321-c0bc30f1.md)

The deployment agent was mentioning Nixpacks as a build strategy option to users during app deployment conversations. Root cause: the `deploy_app` MCP tool schema listed `"How to build: dockerfile, nixpacks, or buildpack"` in the `build_strategy` parameter description, and `build_strategy` was in the `required` list — so the LLM had to pick one and naturally surfaced all three options to the user.

This contradicts the design intent documented in `deployment_agent_design.md`: "Always containerize. User never chooses Nixpacks vs Docker." The system prompt had no guidance to override what the tool schema was telling the LLM.

**Fix: hide from agent, keep in backend** — Removed `build_strategy` from the tool schema `properties` entirely so the agent never sees it. Hard-defaulted `build_strategy="dockerfile"` in the `mcp_tools.py` handler. Made `dockerfile_path` required instead (since a Dockerfile is always needed now). The `App.BuildStrategy` model enum still has `nixpacks` and `buildpack` options for future use — they're just invisible to the agent.

**Key points:**
- Tool schemas are a powerful influence on LLM behavior — if you list options, the LLM will mention them. Removing unused options from the schema is more reliable than adding system prompt instructions to "don't mention X"
- The `dockerfile_path` description was also cleaned up from "Required for dockerfile build strategy" to just the path description, since there's no other strategy to contrast against
- The `deploy_app.py` backend function docstring was updated to note "currently only 'dockerfile'" for future developers

---

## 2026-02-06 03:05 - [AgentChat] System prompt restructuring with XML tags per Claude prompt engineering docs

**Conversation:** [2026-02-05-2307-3796616f.md](conversations/2026-02-05-2307-3796616f.md)

Audited `system_prompt_app_deployment.md` against every recommendation in Anthropic's Claude prompt engineering documentation (overview, be-clear-and-direct, use-examples, give-claude-a-role, use-xml-tags, chain-of-thought, chain-prompts, long-context-tips, extended-thinking-tips). Produced a prioritized list of 10 improvements, then implemented #2 (XML tags) and parts of #3 (stronger goal) and #10 (consolidation).

**XML tag restructuring** — Replaced all markdown headers (`##`, `###`) with semantic XML tags: `<role>`, `<goal>`, `<formatting>`, `<deployment_flow>`, `<environment_selection>`, `<no_environment>`, `<repository_analysis>`, `<app_secrets>`, `<existing_apps>`, `<domain_naming>`, `<infrastructure_decisions>`, `<pre_deployment_checklist>`, `<polling>`, `<question_philosophy>`, `<names_vs_uuids>`. The docs say XML tags "reduce errors from Claude misinterpreting parts of your prompt" compared to markdown headers, and recommend referring to tag names in instructions (e.g., "see `<environment_selection>` rules" instead of "see Environment Selection below").

**Dynamic sections also tagged** — Updated `agent_service.py` so the runtime-appended context uses XML: `<conversation_context>` (with nested `<workspace>`, `<repository>`, `<aws_account>`), `<aws_infrastructure>` (with nested `<aws_account>` and `<environment>`), `<existing_environments>` (with nested `<environment>`). This affected all three prompt builders (app deployment, environment, general).

**Child tags over attributes** — Initially used XML attributes (`<environment name="dev" id="uuid" .../>`) for the nested data. Switched to child tags (`<name>dev</name>`, `<id>uuid</id>`) after realizing the documentation's examples consistently use child tags (the `<document><source>...</source>` pattern), and I couldn't find doc evidence that attributes are parsed more reliably. Honesty about what the docs actually say matters when making prompt engineering decisions — don't extrapolate claims beyond evidence.

**Goal rewrite** — The original goal was process-oriented ("analyze, configure, and deploy"). Rewrote to be outcome-oriented per the docs' recommendation to define "what a successful task completion looks like": successful conversation ends with the app deployed and accessible at its URL; when not possible, give user a clear next step. Added audience context (user understands their app but may not know AWS internals) and workflow position (user clicked "New App Deployment" — they're ready to ship).

**Section consolidation** — Merged `<redeployment>` (2 bullet points about tool mechanics) into `<existing_apps>` since both handle the "app already exists" case. Eliminated a tag that was too small to justify its own section.

**Key points:**
- The docs' tag name cross-referencing tip ("Using the contract in `<contract>` tags...") was applied throughout — `<deployment_flow>` references `<aws_infrastructure>`, `<environment_selection>`, `<existing_apps>`, `<conversation_context>` by tag name
- Remaining improvements from the audit (not yet implemented): #1 multishot examples (highest impact), #3 role strengthening, #5 chain-of-thought for complex decisions, #6 success criteria, #7 replace vague personality directives
- The XML restructuring is consistent across all three system prompts (`system_prompt_general.md` also updated its one reference to `<aws_infrastructure>`)

## 2026-02-06 01:50 - [DevEx] CLI test harness parity with web experience

**Conversation:** [2026-02-05-1749-102fd7ae.md](conversations/2026-02-05-1749-102fd7ae.md)

The `test_main_agent.py` CLI harness was missing critical context that the web UI provides, making it useless for testing APP_DEPLOYMENT and ENVIRONMENT_SETUP flows. The web's `chat_new` view sets workspace, repository, and AWS account context on the conversation, derives mode from that context, and creates a SYSTEM_TRIGGER message for auto-start modes — the CLI did none of this, always creating bare GENERAL conversations.

The fix involved several layers:

**Context resolution** — Added `--workspace` (default: "default"), `--repo` (default: "vmendi/ai-detector-and-humanizer"), and `--aws-account` CLI args. Each resolves by name (case-insensitive) or UUID against the user's organization. Added `--no-context` to strip all context for pure GENERAL mode testing, and `--mode` to override auto-derivation.

**Centralized conversation creation** — During implementation, we noticed the trigger content map and mode derivation logic were being duplicated between `chat.py` and the CLI. Extracted `create_conversation()` into `agent_service.py` as the single source of truth. It takes `mode: str | None` — when `None`, it auto-derives from context fields (aws_account → ENVIRONMENT_SETUP, workspace+repo → APP_DEPLOYMENT, else GENERAL). Both `chat_new` and the CLI now call this one function. The trigger content map is a local inside the method since nothing else needs it.

**Auto-start for trigger messages** — The web UI auto-starts the agent when a SYSTEM_TRIGGER message exists. The CLI now checks for a trigger message after conversation creation and runs `_stream_agent` before entering the REPL or processing `--prompt`. This required splitting `_run_agent_once` into `_stream_agent` (streams response for whatever the last message is) and `_run_agent_once` (creates user message + streams).

**REPL quality of life** — Prints conversation ID after each turn for easy copy-paste into `--conversation-id` for subsequent runs.

**Key points:**
- Default experience is now APP_DEPLOYMENT with workspace "default" + repo "vmendi/ai-detector-and-humanizer" — matches the most common test scenario
- `create_conversation()` in `agent_service.py` owns mode derivation + trigger creation — DRY across web view and CLI
- Sync-only `create_conversation()` — the CLI wraps it with `sync_to_async` rather than maintaining a parallel async version
- Auto-start streams the trigger response before `you>` prompt, matching web behavior exactly
- `--no-context` and `--mode` provide escape hatches for testing edge cases (GENERAL mode, forced mode override)

## 2026-02-05 23:45 - [AgentChat] Real-time sidebar cost update via OOB swap

**Conversation:** [2026-02-05-1414-b98ede79.md](conversations/2026-02-05-1414-b98ede79.md)

Added dynamic cost updates to the chat sidebar so the "Agent Cost:" value refreshes in real time as the agent completes each turn, rather than only showing the cost at page load.

The implementation follows the same OOB swap pattern already used for title updates (`_render_title_update`). The agent service queries the cumulative conversation cost from `LLMUsageLog` via a `SUM` aggregate after each turn completes, and includes `total_cost` in the `complete` event data. On the view side, a new `_render_cost_update()` function generates an OOB swap targeting a stable `id="sidebar-cost-{conversation_id}"` element. The `_format_sse_event` function gained a `show_costs` parameter, and the `chat_stream` view passes `user.is_staff` into it — non-admin users never receive cost swap HTML.

A subtle template change was needed: the sidebar cost `<p>` element now always renders (with a stable `id`) when `show_costs` is true, even if there's no cost yet. This ensures the OOB swap has a target on the first turn of a new conversation. Previously, the element only rendered when `conv.total_cost` was truthy, which meant the first cost update had nothing to swap into.

**Key points:**
- OOB swap pattern mirrors `_render_title_update` — proven pattern for sidebar updates during streaming
- Sidebar cost element always renders with stable `id` when `show_costs=True` (empty if no cost) so the first OOB swap has a target
- `_format_sse_event` now takes `show_costs` param — keeps admin gating in the view layer, not the agent service
- Agent service queries `SUM(cost_usd)` on complete — one extra query per turn, negligible overhead

## 2026-02-05 23:10 - [AgentChat] Add LLM usage cost tracking to database

**Conversation:** [2026-02-05-1405-b98ede79.md](conversations/2026-02-05-1405-b98ede79.md)

Added an `LLMUsageLog` model to record every LLM interaction for cost tracking and future billing. The design went through a deliberate options discussion: (A) dedicated log model, (B) fields on Conversation, (C) hybrid. Chose Option A — a dedicated append-only log table — because it provides per-interaction granularity, avoids race conditions on accumulation, and cleanly handles future LLM call sites beyond agent turns.

The model tracks organization, user, conversation (nullable FK with SET_NULL so cost data survives conversation deletion), source type (agent_turn or title_generation), model alias/ID, input/output tokens, cost in USD, duration, and number of turns. Two indexes support billing rollup queries: `(organization, created_at)` and `(conversation, created_at)`.

Two call sites were wired up:

- **Agent turns** — The Claude Agent SDK's `ResultMessage` already provides `total_cost_usd`, `usage` dict, `duration_ms`, and `num_turns`. These were previously only logged to stdout. Now an `LLMUsageLog` row is created after each `ResultMessage`.
- **Title generation** — The raw Anthropic API doesn't return cost (only the Agent SDK computes that), so `title_generator.py` was refactored to return a `TitleResult` dataclass with `title`, `input_tokens`, and `output_tokens`. Cost is stored as NULL for title generation rows; the token counts are available for retroactive computation if needed.

Per-conversation cost is displayed in the chat sidebar and workspace detail conversation list, gated on `request.user.is_staff` (Django admin flag). The cost comes from a `SUM` annotation on the conversation queryset — a single JOIN + GROUP BY, no N+1 queries. Non-admin users see no difference. Real-time cost updates during streaming were discussed but deferred to a follow-up.

**Key points:**
- Chose append-only log model over accumulator fields on Conversation — better granularity, no race conditions, extensible to future LLM call sites
- Organization FK uses CASCADE (consistent with all other org-owned models); Conversation and User FKs use SET_NULL to preserve cost data
- Raw Anthropic API doesn't expose cost_usd — only the Claude Agent SDK's ResultMessage computes it. Title generation logs tokens but cost is NULL
- Admin-only display uses `is_staff` flag — simplest gating mechanism, no feature flags needed
- Real-time sidebar cost updates (via OOB swap on stream complete) identified as natural follow-up but deferred

## 2026-02-05 20:30 - [Bugfix] Fix Claude Opus 4.6 Bedrock model ID for inference profile

**Conversation:** [2026-02-05-1227-f844bdc7.md](conversations/2026-02-05-1227-f844bdc7.md)

After upgrading to Claude Opus 4.6, the agent started returning `400 Invocation of model ID anthropic.claude-opus-4-6-v1 with on-demand throughput isn't supported` errors. The root cause: starting with Claude Sonnet 4.5 and all subsequent models, AWS Bedrock requires cross-region inference profile IDs rather than raw model IDs for on-demand invocation.

The `LLM_MODELS` dict in `llm_client.py` had the Opus 4.6 Bedrock ID set to `anthropic.claude-opus-4-6-v1` (raw model ID, no inference profile prefix). All other models already used the `us.` prefix correctly (e.g., `us.anthropic.claude-opus-4-5-20251101-v1:0`).

The first fix attempt used `us.anthropic.claude-opus-4-6-v1:0` (adding both the `us.` prefix and a `:0` version suffix by analogy with the older models). This produced a different error: `400 The provided model identifier is invalid.` The `:0` suffix was the problem — Opus 4.6 uses a simplified ID format without the version suffix and without a date stamp, unlike the 4.5-era models.

The correct Bedrock inference profile ID, confirmed from the AWS Bedrock docs (inference-profiles-support.html), is `us.anthropic.claude-opus-4-6-v1` — with the `us.` prefix but no `:0`.

**Key points:**
- AWS Bedrock requires inference profile IDs (`us.`, `eu.`, `global.` prefix) for all Claude models from Sonnet 4.5 onward — raw model IDs (`anthropic.claude-*`) return "on-demand throughput isn't supported"
- Opus 4.6 uses a new simplified naming convention: no date stamp, no `:0` version suffix (base ID is `anthropic.claude-opus-4-6-v1`, not `anthropic.claude-opus-4-6-20260205-v1:0`)
- Always check the AWS Bedrock inference profiles docs directly — Anthropic's own docs listed `anthropic.claude-opus-4-6-v1:0` (with `:0`) as the Bedrock ID, but the actual AWS inference profile is `us.anthropic.claude-opus-4-6-v1` (without `:0`)
- The two-attempt debugging sequence is a good reminder: when a model ID fails, the error message tells you which part is wrong (missing inference profile vs. invalid identifier format)

## 2026-02-05 13:45 - [Bugfix] Handle API errors from Claude Agent SDK AssistantMessage

**Conversation:** [2026-02-05-1215-cf8bd08f.md](conversations/2026-02-05-1215-cf8bd08f.md)

Discovered that `_handle_assistant_message` silently dropped API-level errors from the Claude Agent SDK. When the SDK returns an `AssistantMessage` with `message.error` set (e.g., `invalid_request`, `rate_limit`, `server_error`), the error description arrives inside a `TextBlock` in `message.content`. However, unlike the happy path, this `TextBlock` was **never streamed** via `SDKStreamEvent` — the streaming events only fire for successful responses. The old code assumed all `TextBlock` content had already been streamed and skipped it, so API errors vanished silently and the conversation just hung.

The fix adds an early-return guard at the top of `_handle_assistant_message`: if `message.error` is set, extract the error text from the `TextBlock` content, log it, persist it to the database, and yield an error event to the UI.

During the fix, we also corrected a misleading comment that claimed `ThinkingBlock` content was "streamed via SDKStreamEvent." Inspecting `_handle_sdk_stream_event` confirmed it only handles `text_delta` events — thinking content is never streamed. The skip is still correct (we intentionally don't surface thinking to users), but the comment was inaccurate.

Finally, we unified two nearly-identical functions (`_persist_error` taking an `Exception` and `_persist_api_error` taking strings) into a single `_persist_error(conversation, error_type, error_description)` that both call sites use. The Exception-based caller now does `type(e).__name__` and `f"Agent error: {e}"` at the call site instead of inside the function.

**Key points:**
- `AssistantMessage.error` can be any of: `authentication_failed`, `billing_error`, `rate_limit`, `invalid_request`, `server_error`, `unknown` — all were silently ignored before this fix
- The `TextBlock` inside an error `AssistantMessage` is NOT pre-streamed, unlike the happy path — this was the core incorrect assumption
- `ThinkingBlock` is never streamed via `SDKStreamEvent`; we skip it intentionally (not surfaced to users), not because it was already streamed
- Consolidated two persist-error functions into one with a string-based interface, eliminating duplication

## 2026-02-04 14:06 - [ControlPlane] Aurora Serverless cold start causing production timeouts

**Conversation:** [2026-02-04-1407-64239c6e.md](conversations/2026-02-04-1407-64239c6e.md)

User reported production website (devopshero.ai) was timing out after 10+ seconds. Systematic diagnosis through ECS, ALB, and Aurora metrics revealed the root cause: Aurora Serverless v2 cold start latency.

**Investigation process:**

1. **Initial checks** — ECS service healthy (1/1 running), ALB target healthy, Aurora cluster "available"
2. **Found the smoking gun in ALB metrics** — `TargetResponseTime` showed:
   - 21:56 UTC: 30-second timeout (average and max both 30s — hitting ALB timeout limit)
   - 21:59 UTC: 17-second max response time
   - All other periods: 4-5ms average
3. **Aurora Serverless capacity metrics** — Running at minimum 0.5 ACUs most of the time, with occasional spikes to 2-4 ACUs under load

**Root cause:** Aurora Serverless v2 at 0.5 ACU minimum capacity goes cold during idle periods. When the next request arrives, the database needs to scale up, adding 10-30 seconds of latency. This is especially problematic after traffic spikes (saw 500+ req/min at 21:45-21:47) followed by near-zero traffic.

**Contributing factor:** SSE connection cleanup logs showed `db_connections 15` being returned to pool, while CloudWatch showed only 4 Aurora connections. This suggests connection pool fragmentation, though not the primary cause of timeouts.

**Fix:** Increased `serverless_v2_min_capacity` from 0.5 to 1 ACU in `infra_devopshero/stacks/database_stack.py`. This doubles the baseline capacity and significantly reduces cold start latency. The cost increase is minimal (~$43/month vs ~$22/month at idle) but eliminates the 10-30 second cold start penalty.

**Key debugging commands used:**
- `aws cloudwatch get-metric-statistics` for ALB TargetResponseTime, Aurora ServerlessDatabaseCapacity, CPUUtilization
- `aws logs tail /devopshero/prod/ecs --since 10m` for real-time log inspection
- `aws elbv2 describe-target-health` for ALB health checks
- `aws rds describe-db-clusters` for Aurora status

**Key points:**
- Aurora Serverless v2 at 0.5 ACU minimum is too aggressive for user-facing production workloads — cold start can take 10-30 seconds
- ALB access logs were disabled, making it harder to identify which specific URLs caused timeouts — consider enabling
- The prod-debug skill provides a good starting framework but needed CloudWatch metrics commands for this diagnosis

## 2026-02-04 06:15 - [Bugfix] GitHub integration Re-sync button doesn't work when status is ERROR

**Conversation:** [2026-02-03-2201-53c9f06e.md](conversations/2026-02-03-2201-53c9f06e.md)

Debugged why the GitHub integration showed "Error" status locally and why the Re-sync button did nothing.

**Bug 1: Re-sync only works when already connected**

The `settings_git_integrations` view had a condition that only ran sync if status was already CONNECTED:

```python
if integration and integration.status == GitProviderIntegration.Status.CONNECTED:
    github_client.sync_repositories(...)
```

This meant clicking Re-sync on an ERROR status integration did nothing — the button appeared to work (POST request succeeded) but the sync was silently skipped.

**Fix:** Changed the condition to check for `installation_id` instead of status, and added proper error handling that updates status based on sync result:
- Sync succeeds → status becomes CONNECTED
- Sync fails → status becomes/stays ERROR (with error logged)

**Bug 2: Stale installation ID causes 404**

After fixing the Re-sync button, clicking it produced:
```
Client error '404 Not Found' for url 'https://api.github.com/app/installations/105666021/access_tokens'
```

The local database had installation ID `105666021`, but that installation no longer existed on GitHub (likely uninstalled at some point). The GitHub App credentials were correct, but the installation reference was stale.

**Workaround:** Queried production for the valid installation ID using `doh_query`:
```bash
./prod_manage.sh doh_query GitProviderIntegration installation_id status provider
# Result: 106276726 | connected | github
```

Then updated local database to use production's installation ID. This works because both local and prod use the same GitHub App (same `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY`), so they can share the installation.

**Key points:**
- Re-sync should attempt sync regardless of current status — the whole point of "re-sync" is to recover from problems
- GitHub App installation IDs are org-specific but can be shared across environments using the same GitHub App credentials
- When GitHub integration shows ERROR locally, "Reconnect" (full OAuth flow) is the proper fix; copying installation ID from prod is a shortcut that works if using shared credentials

## 2026-02-03 15:30 - [DevEx] Rename doh_control retry commands for clarity

**Conversation:** [2026-02-03-1941-3dce166b.md](conversations/2026-02-03-1941-3dce166b.md)

Refactored `doh_control` management command to use more explicit, self-documenting command names for retry operations.

**Changes:**

- `provision-env` → `retry-env-provisioning` — The old name was confusing because it sounded like initial provisioning rather than retrying a failed one. The new name makes it explicit this is a retry operation.
- `retry-deployment` → `retry-app-deployment` — Clarifies this retries an *app* deployment specifically, not an environment deployment or other type.
- Removed `--provision` flag from `create-env` — There was a bug where the flag did nothing (status was always set to PENDING regardless). Rather than fix the bug, we made PENDING the only behavior since you create an environment to use it, not to let it sit idle.

**Rationale:**

The naming now follows a consistent pattern where retry commands explicitly state what's being retried:
- `retry-env-provisioning` — retry failed environment provisioning
- `retry-app-deployment` — retry failed app deployment

This eliminates ambiguity. "provision-env" could have meant "provision this environment for the first time" vs "re-provision/retry". The new name is unambiguous.

**Key points:**
- Updated both `doh_control.py` and the `prod-manage` skill documentation
- Fixed status messages to say "queued for retry" instead of technical "set to PENDING"
- Created sandbox environment in production for Humanity Rules organization using Humanity Rules Sandbox AWS account (chsandbox.com hosted zone)

## 2026-02-04 10:45 - [UI] Show deployed environments on workspace app tiles

**Conversation:** [2026-02-03-1924-eb36c2ae.md](conversations/2026-02-03-1924-eb36c2ae.md)

Added environment indicators to app tiles in the workspace detail view, showing which environments each app is currently deployed to.

**Implementation:**

Modified `workspace_detail` view to prefetch active deployments (status=RUNNING) with their environments using Django's `Prefetch` object. This avoids N+1 queries by loading all deployment/environment data in a single optimized query. The prefetched data is stored in `app.active_deployments` attribute.

Updated the workspace detail template to display environment names as green badges on each app tile. Apps without active deployments show an em dash (—) instead.

**Style refactor:**

During implementation, noticed the workspace app tiles had inconsistent styling compared to the dashboard. The original style had inline "Field: value" text, while the dashboard uses a clean two-column layout with labels left-aligned and values right-aligned. Refactored the entire app tile structure to match the dashboard pattern:

- Header row with app name and type badge
- `space-y-2 text-sm` container for info rows
- Each row uses `flex items-center justify-between`
- Labels in muted gray, values in lighter gray
- Environment badges right-aligned with `flex-wrap justify-end`

This ensures visual consistency across the dashboard and workspace detail views.

**Key points:**
- Only RUNNING deployments are shown — excludes pending, failed, and teardown statuses
- Used `Prefetch` with `to_attr` to create a clean `active_deployments` list attribute
- Em dash (—) for empty state matches dashboard convention for missing status

## 2026-02-03 23:15 - [DevEx] Management command and skill cleanup

**Conversation:** [2026-02-03-1739-7d1d03f5.md](conversations/2026-02-03-1739-7d1d03f5.md)

Major cleanup of the `doh_*` management commands and Claude skills to improve naming consistency, reduce duplication, and consolidate query operations.

**Command renames:**

- `doh_customer` → `doh_control` — Better reflects "control plane operations" (create-env, provision-env, teardown-env, retry-deployment)
- `doh_deploy` → `doh_direct` → `doh_raw` — Evolved through discussion; "raw" best conveys bypassing the normal UI/DB/job-worker flow for direct CDK access
- `list` → `list-infra` — More specific than generic "list" (though later removed entirely)

**Query consolidation:**

Removed all read-only operations from `doh_control` (formerly `doh_customer`):
- `list-infra` (was `list`)
- `list-apps`
- `list-deployments`
- `deployment-logs`

These are now handled by `doh_query` which already supports all models. Updated `doh_query` docstring with common query examples for Organization, AWSAccount, Environment, App, Deployment, DeploymentLog. Tested all documented queries to verify they work.

**Final command taxonomy:**
- `doh_query` — Read-only ad-hoc queries on any model
- `doh_control` — Control plane mutations (env/deployment operations)
- `doh_raw` — Direct CDK deployment bypassing normal flow

**Skill improvements:**

- Updated `prod-manage/SKILL.md` with current command names and examples
- Expanded `prod-debug/SKILL.md` with practical debugging examples (ECS status, CloudWatch logs, ALB health, CloudFormation stacks)
- Fixed incorrect target group name (`doh-prod-tg` → `doh-prod-app-tg`) discovered during testing

**Key principles applied:**
- Commands should have clear, distinct purposes (query vs mutate vs bypass)
- Active documentation (SKILL.md, AGENTS.md) must be updated; historical docs (journal, conversations) left as-is
- All documented examples should be tested to catch errors like wrong resource names

## 2026-02-03 21:45 - [DevEx] Fix job worker starting during prod_manage.sh commands

**Conversation:** [2026-02-03-1420-ceb0fdee.md](conversations/2026-02-03-1420-ceb0fdee.md)

When running management commands via `prod_manage.sh` (which uses `aws ecs execute-command` to run commands in the production container), the job worker was starting up unexpectedly. This caused duplicate workers competing for jobs.

**Root cause analysis:**

The production ECS container has `DOH_RUN_JOB_WORKER=1` set in its environment. When `prod_manage.sh` runs a command like `doh_customer list`, it spawns a **new Python process** inside the existing container via ECS Exec. This new process goes through Django's `AppConfig.ready()` initialization, sees the env var is set, and starts its own job worker thread.

So we'd have:
1. The main Gunicorn process with its job worker (correct)
2. Each management command spawning its own short-lived job worker (wrong)

These extra workers would compete with the main one for claiming jobs, and die when the management command exits.

**Solution:**

Added `_is_management_command()` detection in `apps.py` that checks if running via `manage.py`. The job worker now only starts for web server processes:

- **Gunicorn** — `sys.argv[0]` is the gunicorn executable, not `manage.py`
- **`manage.py runserver`** — Explicitly allowed for local development
- **Other management commands** — Worker skipped

The "blocklist" approach (detect `manage.py`) is more robust than an "allowlist" (detect specific servers) because it automatically works with any WSGI/ASGI server without needing to enumerate them.

**Key points:**
- `prod_manage.sh` doesn't spin up new containers; it uses ECS Exec to run commands in the existing container
- Each `manage.py` invocation is a separate Python process with its own Django initialization
- The fix detects `sys.argv[0].endswith('manage.py')` and skips worker startup (except for `runserver`)

## 2026-02-03 14:30 - [Deployment] Environment teardown feature

**Conversation:** [2026-02-03-1424-677f28ff.md](conversations/2026-02-03-1424-677f28ff.md)

Implemented environment teardown functionality that deletes all infrastructure and removes records from the database. The feature runs asynchronously via the existing job worker infrastructure.

**Teardown flow:**
1. User triggers teardown via CLI (`doh_customer teardown-env`) or agent tool
2. Environment status set to `TEARDOWN_PENDING`
3. Job worker picks up pending teardown, sets status to `TEARING_DOWN`
4. All deployments in the environment are torn down sequentially (stop on first failure)
5. Base infrastructure stacks deleted (cluster, builder, VPC)
6. Environment record deleted from database

**Key design decisions:**

- **Delete records instead of TORN_DOWN status** — Initially implemented with a `TORN_DOWN` status for audit trail, but decided to delete records instead. Rationale: the records are just references to infrastructure that no longer exists. If audit history is needed, logs/events serve that purpose better. This keeps the database clean and allows slug reuse.

- **Always force teardown** — Removed the `--force` flag that would check for active deployments. Teardown always proceeds regardless of deployment states. Simplifies the API and matches the "delete everything" semantics.

- **File renames for clarity** — Renamed executor files to be explicit about what they operate on:
  - `teardown_executor.py` → `app_deployment_teardown_executor.py` (tears down a deployment, not an app)
  - `deployment_executor.py` → `app_deployment_executor.py`
  - `environment_executor.py` → `environment_provisioning_executor.py`
  - Similarly renamed functions in `job_worker.py` (e.g., `_claim_pending_teardown` → `_claim_pending_app_deployment_teardown`)

- **Log before delete** — Fixed a bug where logging after `environment.delete()` failed because the `EnvironmentLogContext` handler tried to create a log record with a deleted foreign key. Solution: log the success message before calling delete.

**Key points:**
- Environment has `TEARDOWN_PENDING` and `TEARING_DOWN` statuses but no `TORN_DOWN` (record is deleted on success)
- Deployment teardown also deletes the record on success
- Sequential teardown with stop-on-first-failure prevents orphaned infrastructure
- Added `teardown-env` subcommand to `doh_customer` management command
- Added `teardown_environment` agent tool for UI/agent integration

## 2026-01-31 15:57 - [Integrations] PostHog only when DEBUG is False

**Conversation:** [2026-01-31-1557-6179b2b2.md](conversations/2026-01-31-1557-6179b2b2.md)

PostHog analytics (frontend and backend) is now disabled whenever `DEBUG=True`, so local development never sends events or initializes the SDK even if `POSTHOG_API_KEY` is set in `.env`.

**Changes:**
- **`devopshero_app/apps.py`** — `_init_posthog()` runs only when `posthog_key and not settings.DEBUG`. The Python SDK (exception autocapture) is not initialized in DEBUG mode.
- **`devopshero_app/context_processors.py`** — `posthog_context()` returns `{'posthog_config_json': None}` when `not api_key or settings.DEBUG`, so the base template does not render the PostHog script tag and no client-side tracking runs.

**Key points:**
- Activation still requires `POSTHOG_API_KEY`; DEBUG is an additional gate. Production (DEBUG=False) with the key set continues to use PostHog as before.
- Single source of truth: Django’s `DEBUG` flag, no separate “is production” env needed for this behavior.

## 2026-01-31 17:13 - [UI] Dashboard app card revamp with deployment info

**Conversation:** [2026-01-31-1514-f3894281.md](conversations/2026-01-31-1514-f3894281.md)

Revamped the app cards on the dashboard to be more informative and navigable. The cards now link to the app detail page and show deployment status at a glance.

**Changes made:**
- **Clickable app name** — The app name is now an HTMX link to `/apps/<slug>/` instead of plain text, matching the navigation pattern used elsewhere.
- **Labeled fields** — Each field (Repository, Branch, Workspace) now has a title on the left with the value on the right, using a consistent key-value layout with `flex justify-between`.
- **Type badge retained in header** — The app type capsule stays next to the app name in the header row rather than moving to the field list.
- **Last deployed field** — Shows relative time (e.g., "2 days ago") using Django's `timesince` filter, or "Never" if no deployments exist. View annotates apps with `Max("deployments__created_at")`.
- **Status field** — Shows the latest deployment status as a colored badge (green=running, yellow=in-progress, red=failed). Uses a `Subquery` to fetch the status from the most recent deployment per app.

**Key points:**
- App model has no status field; status comes from the latest Deployment, requiring a subquery annotation.
- The `latest_status` annotation uses `Subquery` with `OuterRef` to get the status of the newest deployment for each app.
- Color coding follows the same pattern as datastore status badges (green/yellow/red/gray).

## 2026-01-31 16:30 - [UI] App detail view and configuration sections

**Conversation:** [2026-01-31-1453-1c304568.md](conversations/2026-01-31-1453-1c304568.md)

Added an app detail page reachable from the workspace detail view (clicking an app card) and structured configuration into separate cards instead of one large block.

**App detail view:**
- New route `/apps/<slug>/` with `app_detail` view in `views/apps.py`.
- Breadcrumb: Workspaces → Workspace name → (current app in title).
- Workspace detail app cards are now links with HTMX (same pattern as environments/workspace detail).

**Configuration layout:**
- Replaced a single "Configuration" card with smaller sections so labels stay fixed and empty values show "—" instead of sections appearing/disappearing.
- **Source** — Repository, Branch, Subpath (side-by-side with Build on larger screens).
- **Build** — Strategy, Dockerfile.
- **Container** — Port, CPU (units + vCPU), Memory, Health Check Path, Health Check Command.
- **Connections** — Datastore, Secrets (keys only).
- **Environment Variables** — Own section; always shown with empty state when none.
- Section headings use `text-base font-semibold` for prominence.
- CPU vCPU: computed in view as `app.cpu / 1024`, displayed next to units (e.g. "1024 units (1.0 vCPU)").
- Created/Created By moved from a floating metadata line into the header block under the repo/branch line so it feels attached to the app identity.

**Key points:**
- All app fields are shown in a stable layout; empty values render as "—".
- Field order: Repository, Branch, Subpath, then Build (Strategy, Dockerfile), then Container, then Connections; Repository and Branch added to config so repo is visible in the grid.
- `get_app_type_display` is Django’s auto-generated method for `choices` fields (returns human-readable label).

## 2026-01-31 14:15 - [UI] Landing page full-page section scrolling disabled

**Conversation:** [2026-01-31-1411-7c55c255.md](conversations/2026-01-31-1411-7c55c255.md)

Disabled the full-page section snapping on the landing page by commenting out the wheel event listener instead of removing it, so the behavior can be re-enabled easily later.

**What was disconnected:** A wheel listener was intercepting scroll events (`e.preventDefault()`), accumulating `deltaY`, and calling `scrollToSection()` to snap to the next/previous `.landing-section`. That hijacked normal browser scrolling in favor of one-section-at-a-time snapping.

**Minimal change:** Only the single `window.addEventListener('wheel', ...)` block (the one with `passive: false` and the section-index logic) was commented out. Helpers (`scrollToSection`, `getCurrentSectionIndex`, `wheelAccumulator`, etc.) were left in place so they are unused but harmless. A comment was added above the block: "Disabled: full-page section snapping (wheel hijacks scroll and snaps to sections)."

**Why comment instead of delete:** Per user request — disconnect the behavior without removing code, so it can be restored by uncommenting.

**Key points:**
- Smooth scrolling via `scroll-smooth` on `<html>` and anchor links is unchanged; only the wheel-based section snapping was disabled.
- The second wheel listener (accumulator reset after 150ms) remains active but has no effect once the main listener is disabled.

## 2026-01-31 12:45 - [DevEx] CDK CLI output control for programmatic invocation

**Conversation:** [2026-01-31-0230-e42f8c01.md](conversations/2026-01-31-0230-e42f8c01.md) 

When invoking CDK via subprocess from Python, the default progress bar output renders poorly — it uses terminal cursor manipulation optimized for interactive use, resulting in repeated/garbled lines when captured as stream text.

**The problem:** CDK's default `--progress bar` mode shows a progress bar that updates in place. When piped through subprocess, each "update" becomes a separate line with the same content, creating noise instead of useful progress information.

**Solution: Two CLI flags**

1. **`--progress events`** — Shows CloudFormation events line-by-line instead of a progress bar. Each event is a discrete line, perfect for logging.

2. **`--ci`** — Indicates CI environment. The key behavioral change: logs go to stdout instead of stderr.

**Why stderr was weird:** CDK by default sends deployment output to stderr (not stdout), which is why `cdk_utils.py` had this special case:

```python
if source == "cdk" and stream_name == "stderr":
    level = _cdk_level_for_line(cleaned)
```

With `--ci`, everything goes to stdout, which is more logical.

**Code simplification:** With merged streams, we no longer need:
- Threading (was running parallel stdout/stderr readers)
- Generic `_stream_process_output` function with stream_name parameter
- Special stderr detection logic

The new code uses `stderr=subprocess.STDOUT` to merge streams and a simple `_stream_cdk_output` function that reads stdout and detects error lines by content (FAILED, ROLLBACK, ERROR, CANCELLED tokens).

**Other CDK output options discovered:**
- `--no-color` — Removes ANSI color codes
- `--verbose` / `-v` — Increases detail (stackable)
- `cdk.json` can set `"progress": "events"` as default
- CDK Toolkit Library (`@aws-cdk/toolkit-lib`) exists for TypeScript/JS but not Python — allows programmatic control via `IIoHost` interface

## 2026-01-31 11:15 - [AgentChat] Prompt patterns for re-deploy vs deploy distinction

**Conversation:** [2026-01-31-0117-2ea6c7d1.md](conversations/2026-01-31-0117-2ea6c7d1.md)

Observed an excellent deployment agent response and codified the patterns that made it effective into the system prompt. The response clearly distinguished between updating an existing deployment and creating a new one — something users need to understand before taking action.

**What made the response great:**

1. **"← currently deployed here" marker** — When listing environments, the response marked which one already had the app deployed. This immediately orients the user.

2. **Language distinction for actions:**
   - "**Re-deploy to dev** — Push the latest code to the existing deployment" (for environments with the app)
   - "**Deploy to staging** — Create a new deployment in the staging environment" (for environments without)

The verbs "re-deploy" vs "deploy" and the descriptions "push latest code" vs "create new deployment" make the consequences crystal clear. Users know whether they're updating existing infrastructure or spinning up new resources.

**Prompt changes:**

Added a new "Presenting Options for Existing Apps" section to `system_prompt_app_deployment.md` with:
- Concrete example of the environment list format with the deployment marker
- Concrete example of the options format with both re-deploy and deploy variants
- Explicit guidance on when to use each phrasing

Updated deployment flow step 3 to reference this section when an app already exists.

**Key insight:** Good prompts don't just say "be clear" — they provide concrete examples of what clarity looks like in the specific context. The model that produced the great response happened upon this pattern; encoding it ensures consistency.

## 2026-01-31 00:45 - [DomainModel] Subdomain field for multi-environment deployments

**Conversation:** [2026-01-31-0059-a57e0fec.md](conversations/2026-01-31-0059-a57e0fec.md)

Diagnosed and fixed a bug where deploying the same app to multiple environments with the same hosted zone caused Route53 conflicts. The root cause: Route53 domain = `{app_slug}.{hosted_zone}`, so `simple-dashboard` deployed to both `dev` and `staging` would both try to use `simple-dashboard.chsandbox.com`.

**The bug in action:** Conversation `019c12cf-*` showed the agent proposing to deploy `simple-dashboard` to staging when it was already running in dev. The agent had no way to know this would conflict because `list_apps` didn't show which environments apps were deployed to. Conversation `019c12c3-*` showed correct behavior only because the user explicitly asked about deploying to different environments, prompting the agent to suggest a different app name.

**Solution: `subdomain` field on Deployment**

Added a `subdomain` field that controls the Route53 record name, separate from app identity:
- Default: `app.slug`
- Auto-suffixed with `-{env_slug}` if conflict detected (e.g., `simple-dashboard-staging`)
- User can override explicitly via the `subdomain` parameter

**Conflict detection logic:**

The tricky part was getting the exclusion right. Initial implementation excluded all deployments from the same app, which broke the exact scenario we were fixing:

```python
# WRONG: Excludes all app deployments, misses cross-environment conflicts
query.exclude(app_id=app_id)

# CORRECT: Only exclude same app + same environment (we're replacing that deployment)
query.exclude(app_id=app_id, environment_id=environment_id)
```

The key insight: when redeploying to the same environment, we're replacing that deployment so it's not a conflict. But same app to a different environment IS a conflict if they share a hosted zone.

**Enhanced `list_apps` to show deployment info:**

The agent needs visibility into where apps are deployed to make informed decisions. Added `DeploymentInfo` with environment, subdomain, hosted_zone, status, and URL. Returns one deployment per environment (most recent), using Python-level deduplication for SQLite compatibility (PostgreSQL's `DISTINCT ON` isn't portable).

**Files changed:**
- `models.py` — Added `subdomain` field to Deployment
- `tools/deploy_app.py` — Added conflict detection, auto-suffix logic, subdomain parameter
- `tools/list_apps.py` — Enhanced with deployment info per environment
- `infra_customer/deploy_app.py` — CDK uses subdomain for Route53 and ALB routing
- `mcp_tools.py` — Updated tool descriptions
- `system_prompt_app_deployment.md` — Added "Domain Naming and Multi-Environment Deployments" section

**Follow-up task created:** `devopshero-isa` — Add SUPERSEDED status for replaced deployments. Currently, old deployments stay as RUNNING even after being replaced, which causes the list_apps query to scan more rows than necessary.

## 2026-01-31 14:30 - [Deployment] DEBUG-based ECS deployment tuning for faster local iteration

**Conversation:** [2026-01-30-2226-13ef15ba.md](conversations/2026-01-30-2226-13ef15ba.md)

Troubleshooted a deployment that reported failure but actually succeeded. The `simple-dashboard` deploy to the `dev` environment timed out at exactly 180 seconds, but ECS reported the deployment complete at 179 seconds. The root cause was a combination of:

1. The 180-second stabilization timeout being barely sufficient
2. The code requiring 2 consecutive stable checks (STABLE_CHECKS_REQUIRED=2) to confirm deployment success
3. Rolling deployments keeping old tasks running until new ones are healthy

Rather than simply increasing the timeout, we analyzed all ECS timing parameters and implemented DEBUG-based tuning. When DOH runs locally (DEBUG=True), deployments use aggressive settings for fast iteration. When DOH runs in production (DEBUG=False), deployments use stable settings with zero-downtime guarantees.

**Parameters adjusted based on DEBUG flag:**

| Parameter | DEBUG=True | DEBUG=False | Effect |
|-----------|------------|-------------|--------|
| `min_healthy_percent` | 0% | 100% | Old task killed immediately vs zero-downtime rolling |
| `deregistration_delay` | 0s | 30s | No connection draining vs graceful drain |
| `healthy_threshold_count` | 2 | 2 | ALB minimum is 2 — cannot be reduced |
| `health_check_interval` | 5s | 10s | Faster health checks |
| `health_check_grace_period` | 0s | 60s | No grace period vs app warmup time |

Also reduced the `consecutive_failures` threshold from 4 to 3 checks (~15s) for faster fail-fast behavior.

**Key learnings:**

- The "2/1 running" pattern in logs indicates rolling deployment (old + new tasks running simultaneously)
- ALB health checks are the gating factor for deployment speed — container health checks run in parallel and don't affect the happy path
- Container health check parameters (interval, retries, start_period) affect **failure detection speed**, not deployment speed
- ECS `rolloutState=COMPLETED` is the reliable stability indicator, not just `running==desired`
- When using assume role for cross-account access, the `external_id` parameter is required — without it you get "AccessDenied" even with correct credentials

---

## 2026-01-31 10:42 - [DevEx] Add --hosted-zone parameter to doh_deploy CLI

**Conversation:** [2026-01-30-2210-5e432907.md](conversations/2026-01-30-2210-5e432907.md)

The `doh_deploy` CLI command had a misleading comment: `shared_alb_hosted_zone=None,  # CLI uses HTTP-only mode`. This implied the CLI inherently couldn't support HTTPS, but that's not true — the CLI was HTTP-only simply because we hadn't exposed the `shared_alb_hosted_zone` parameter.

When `shared_alb_hosted_zone=None`, the deployment uses path-based routing (`/{app_name}/*`) without HTTPS or DNS records. When a hosted zone is provided, it uses hostname-based routing with HTTPS listener rules and creates a Route53 A record pointing to the shared ALB.

Added the `--hosted-zone` parameter to enable full HTTPS deployments from the CLI:

```bash
# Path-based routing (HTTP only, no domain)
uv run manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox"

# Host-based routing with HTTPS and DNS record
uv run manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox" --hosted-zone dev.example.com
```

**Key points:**
- The "HTTP-only mode" wasn't a CLI limitation — it was just a missing parameter
- Added validation to prevent `--hosted-zone` with `--base` (only makes sense for app deployments)
- Updated the usage docstring with an example
- Removed the misleading comment that implied causality between CLI and HTTP-only

---

## 2026-01-31 05:05 - [Bugfix] Missing ALB DNS name for Route53 alias records

**Conversation:** [2026-01-30-2152-d16a6306.md](conversations/2026-01-30-2152-d16a6306.md)

Follow-up fix to the previous journal entry (2026-01-31 19:45). When deploying `simple-dashboard` to the `dev` environment, CDK synthesis failed with:

```
RuntimeError: 'loadBalancerDnsName' was not provided when constructing Application Load Balancer doh-dev-simple-dashboard-app/ImportedSharedAlb from attributes
```

This is the sibling bug to the canonical hosted zone ID fix. The `from_application_load_balancer_attributes` method requires THREE attributes when importing an ALB for Route53 alias targets:
1. `load_balancer_arn` — was provided
2. `load_balancer_canonical_hosted_zone_id` — was added in previous fix
3. `load_balancer_dns_name` — **was missing**

The DNS name was already being imported in the same function (`shared_alb_dns` at line 448) but wasn't being passed to the ALB constructor.

**The fix:**

```python
shared_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
    self, "ImportedSharedAlb",
    load_balancer_arn=Fn.import_value(f"{prefix}-shared-alb-arn"),
    security_group_id=self.environment_infra.shared_alb_security_group.security_group_id,
    load_balancer_dns_name=shared_alb_dns,  # ADD THIS
    load_balancer_canonical_hosted_zone_id=Fn.import_value(f"{prefix}-shared-alb-canonical-hz-id"),
)
```

**Secondary fix:** The `doh_deploy` CLI command was also broken — it was missing the `shared_alb_hosted_zone` parameter added with the per-app DNS feature. Added `shared_alb_hosted_zone=None` so the CLI works in HTTP-only mode.

**Key points:**
- No environment updates needed — the DNS export (`{prefix}-shared-alb-dns`) already exists in `deploy_base.py`
- The previous fix (canonical hosted zone ID) was incomplete; both DNS name AND hosted zone ID are required for Route53 alias targets
- When importing CDK constructs for use with other services (like Route53), check all required attributes in the CDK docs, not just the obvious ones

---

## 2026-01-31 19:45 - [Bugfix] Missing ALB canonical hosted zone ID for Route53 alias records

**Conversation:** [2026-01-30-1911-3eee1b79.md](conversations/2026-01-30-1911-3eee1b79.md)

Fixed a deployment failure introduced in the per-app DNS records change. When deploying `simple-dashboard` to the `staging` environment, the CDK synthesis failed with:

```
'loadBalancerCanonicalHostedZoneId' was not provided when constructing Application Load Balancer doh-staging-simple-dashboard-app/ImportedSharedAlb from attributes
```

**Root cause:** When creating Route53 alias records pointing to an ALB, AWS requires the ALB's "canonical hosted zone ID" — this is AWS's internal hosted zone identifier for load balancers (different from the user's Route53 hosted zone). The previous change imported the ALB using `from_application_load_balancer_attributes()` but didn't provide this required parameter.

**The fix:**

1. **`deploy_base.py`** — Export the ALB's canonical hosted zone ID from `EcsClusterStack`:
   ```python
   CfnOutput(self, "SharedAlbCanonicalHostedZoneId", 
             value=self.shared_alb.load_balancer_canonical_hosted_zone_id, 
             export_name=f"{prefix}-shared-alb-canonical-hz-id")
   ```

2. **`deploy_app.py`** — Import it when reconstructing the ALB for Route53 alias targets:
   ```python
   shared_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
       self, "ImportedSharedAlb",
       load_balancer_arn=Fn.import_value(f"{prefix}-shared-alb-arn"),
       security_group_id=self.environment_infra.shared_alb_security_group.security_group_id,
       load_balancer_canonical_hosted_zone_id=Fn.import_value(f"{prefix}-shared-alb-canonical-hz-id"),
   )
   ```

**Key insight:** The canonical hosted zone ID is a fixed AWS value per region for ALBs (e.g., `Z35SXDOTRQ7X7K` for us-east-1). It's available on the ALB construct via `load_balancer_canonical_hosted_zone_id` but must be explicitly exported/imported when crossing stack boundaries.

**Deployment note:** Existing environments need to be recreated (or their cluster stacks updated) to export the new value before app deployments will work. For sandbox environments, delete + recreate is cleaner than in-place updates.

---

## 2026-01-31 11:30 - [AgentChat] Deployment polling and environment selection improvements

**Conversation:** [2026-01-30-1911-6ca796ea.md](conversations/2026-01-30-1911-6ca796ea.md)

Improved agent system prompts to ensure reliable deployment monitoring and proper environment selection.

**Problem 1: Agent stops polling prematurely**

The agent would sometimes stop checking deployment status before it completed, leaving users uncertain about whether their deployment succeeded or failed. The existing guidance was too brief: "Poll with `wait` then `get_deployment_status` until complete or failed."

**Solution:** Added explicit "CRITICAL: Poll Until Terminal State" sections to both `system_prompt_app_deployment.md` and `system_prompt_environment.md` with numbered steps:
1. Call `wait` (10s for deploys, 30s for environments)
2. Call status check tool
3. Repeat until terminal state (DEPLOYED/FAILED or READY/FAILED)
4. 15-minute timeout as safety valve

The timeout prevents infinite loops if something gets stuck while still ensuring the agent follows through on normal deployments.

**Problem 2: Agent assumes environment when multiple exist**

When a user had multiple READY environments, the agent might pick one arbitrarily instead of asking.

**Solution:** Added "Environment Selection" section to `system_prompt_app_deployment.md`:
- No READY environments → guide to create one
- One READY environment → use automatically
- Multiple READY environments → ALWAYS ask user to choose

**Key points:**
- Terminal states for apps: DEPLOYED (success), FAILED (failure)
- Terminal states for environments: READY (success), FAILED (failure)
- 15-minute timeout balances reliability with practical limits
- Environment selection respects user choice when ambiguous

---

## 2026-01-31 10:45 - [Deployment] Per-app DNS records instead of wildcard Route53 entries

**Conversation:** [2026-01-30-1855-0237afda.md](conversations/2026-01-30-1855-0237afda.md)

Fixed a DNS conflict issue where two environments using the same hosted zone would clash. The problem: each environment created a wildcard Route53 record (`*.dev.example.com` → its ALB), so the second environment would either fail or overwrite the first environment's DNS, making its apps unreachable.

**The original architecture:**

- Environment creates wildcard cert `*.dev.example.com` (already reused if exists ✓)
- Environment creates wildcard DNS `*.dev.example.com` → its ALB (CONFLICT!)
- Apps rely on wildcard DNS — no per-app records

**The new architecture:**

- Environment creates/reuses wildcard cert `*.dev.example.com` (unchanged)
- Environment does NOT create any DNS record
- Each app creates its own DNS record: `myapp.dev.example.com` → its environment's ALB

**Changes made:**

1. **`deploy_base.py`** — Removed the `route53.ARecord` that created `*.{hosted_zone}` pointing to the ALB. Also removed the now-unused `aws_route53_targets` import.

2. **`deploy_app.py`** — Added per-app DNS record creation in `_setup_shared_alb_routing`:
   - Added `route53`, `targets`, and `route53_utils` imports
   - Added `shared_hosted_zone_id` parameter to `AppStack`
   - Look up hosted zone ID in `deploy()` before CDK synthesis
   - Import the shared ALB using `Fn.import_value(f"{prefix}-shared-alb-arn")`
   - Create `route53.ARecord` for `{app_name}.{hosted_zone}` → ALB

3. **`system_prompt_environment.md`** and **`mcp_tools.py`** — Updated messaging to accurately describe certificate behavior: "uses a wildcard SSL certificate (creates one if none exists, otherwise reuses the existing certificate)" instead of "creates a wildcard SSL certificate".

**Design decision:** App hostnames are `{app_name}.{hosted_zone}` without environment slug. The expectation is that different environments use different hosted zones (e.g., `staging.example.com` vs `prod.example.com`). If someone deploys the same app name to two environments sharing a hosted zone, the DNS records will conflict — but that's a configuration error.

**Key insight:** The ALB import for Route53 alias targets requires the ALB ARN, which was already exported by `EcsClusterStack`. The `env_slug` passed through the deployment chain determines which environment's ALB to import via the CloudFormation export name pattern `devopshero-{env_slug}-shared-alb-arn`.

---

## 2026-01-30 18:53 - [AgentChat] Switch environment setup model from Sonnet to Opus

**Conversation:** [2026-01-30-1853-a3908fba.md](conversations/2026-01-30-1853-a3908fba.md)

Changed the LLM model for environment setup conversations from Sonnet 4.5 to Opus 4.5. The previous logic used Sonnet for environment setup (considered a simpler task) and Opus for deployment (complex reasoning). Now all agent modes use Opus.

The change was made in `agent_service.py` where model selection happens. The previous conditional logic is preserved as a comment for easy rollback:

```python
# Previous: Use Sonnet for environment setup (simpler task), Opus for deployment (complex reasoning)
# model_alias = "sonnet-4.5" if conversation.mode == Conversation.Mode.ENVIRONMENT_SETUP else settings.CLAUDE_MODEL
model_alias = "opus-4.5"
```

**Rationale:** Testing whether Opus provides better quality responses for environment setup conversations, at the cost of higher latency and token usage.

---

## 2026-01-30 21:45 - [Bugfix] Environment logs cross-talk between concurrent provisioning jobs

**Conversation:** [2026-01-30-1803-2231ca98.md](conversations/2026-01-30-1803-2231ca98.md)

Fixed a bug where `GetEnvironmentStatus` returned logs from other environments. User reported querying staging environment but seeing `devopshero-dev-cluster` logs in the response.

**Initial misdiagnosis:**

At first glance, the query code in `get_environment_status.py` looked correct — it was filtering by `environment=environment`. I made a quick speculative fix changing it to `environment_id=environment.id` thinking async ORM might have issues with object comparison. This was wrong.

**The real issue (found by checking the database):**

User asked me to query the database directly. This revealed the actual problem: every log message was being **duplicated to BOTH environments**. The same CDK output appeared twice — once for staging, once for dev. The query filtering was working correctly; the data itself was corrupted.

**Root cause:**

When multiple environment provisioning jobs run concurrently, both `EnvironmentLogContext` instances attach their handlers to the same root logger (`devopshero_app`). When any code logs a message, ALL attached handlers receive it and write to their respective environments.

```
Job A (staging) enters context → adds Handler A to logger
Job B (dev) enters context → adds Handler B to logger
Any log message → Handler A writes to staging, Handler B writes to dev
```

**The fix:**

Added thread-local context tracking in `job_logging.py`:

1. Module-level `threading.local()` to track active job context per thread
2. Context managers set `_job_context.environment_id` (or `deployment_id`) on enter, clear on exit
3. Handlers check if current thread's context matches their ID before emitting — if not, they skip the log

This isolates logs to their originating job even when multiple jobs run concurrently with handlers attached to the same logger.

**Key lesson:**

When debugging "filter not working" issues, **check the data first**. The query can be correct while the data is corrupted. A 30-second database query would have saved time spent analyzing the query code.

---

## 2026-01-30 20:30 - [AgentChat] Environment setup confirmation flow and naming guidance

**Conversation:** [2026-01-30-1531-8b4f63cc.md](conversations/2026-01-30-1531-8b4f63cc.md)

Fixed two issues discovered when reviewing a real environment setup conversation (019c112b-eb8b-7668-9cf2-6598754f1a11):

**Issue 1: Model didn't wait for user confirmation on region**

The agent said "I'll set this up in us-east-1. Let me know if you need a different region, otherwise I'll proceed" and then *immediately* called `create_environment` in the same turn. The model interpreted this as a polite notification rather than a blocking question — Claude optimizes for efficiency and proceeded when it thought the user would likely accept the default.

**Issue 2: Model used "default" name when one already existed**

The agent called `create_environment` with name "default", but a "default" environment already existed (created a week earlier). The system prompt didn't inject existing environments, so the model had no way to know the name was taken.

**Solution:**

1. **Inject existing environments into system prompt** — `_build_environment_prompt` now queries existing environments and adds context like:
   ```
   ## Existing Environments
   This AWS account already has these environments:
   - default (us-east-1, ready)
   
   Naming priority (use first available): default, dev, staging, prod
   Suggested name: **dev**
   ```

2. **Batched confirmation flow** — After domain selection, agent must present name + region + domain as a package and wait for explicit user confirmation before calling `create_environment`. Changed from implicit "let me know if different" to explicit "does this look good?"

3. **Updated tool description** — Removed the "tell user: let me know if different region" phrasing that encouraged proceeding without waiting. Now explicitly requires confirmation before calling.

**Key insight:** The model needs a *question* that expects an answer, not a statement with an escape hatch. "Let me know if you need different settings" reads as informational, not interrogative.

**Key points:**

- Explicit confirmation flow prevents the model from "helpfully" proceeding without user input
- Injecting existing state (environments, names taken) lets the model make intelligent suggestions
- Naming priority in the prompt (default → dev → staging → prod) eliminates guesswork
- Both name and region are now confirmed together, reducing interaction steps while ensuring user control

---

## 2026-01-30 18:45 - [AgentChat] Conversation modes and mode-specific system prompts

**Conversation:** (to be linked after session)

Major refactor of how conversations work. Introduced explicit conversation modes that determine system prompts, model selection, and auto-triggering behavior. This creates focused, purpose-driven agent experiences rather than one generic assistant trying to do everything.

**The problem:**

The original system prompt was a monolithic 220-line document trying to handle everything: environment creation, app deployment, managing existing apps, connecting AWS accounts. The agent had to figure out what the user wanted based on context clues. When we added `context_aws_account` for environment setup, the prompt became even more complex.

**The solution — Conversation Modes:**

Added a `mode` field to Conversation model with three values:
- **GENERAL** — Default mode for general help, managing existing apps, connecting AWS accounts
- **ENVIRONMENT_SETUP** — Focused on creating environments (user selected AWS account)
- **APP_DEPLOYMENT** — Focused on deploying a repo (user selected workspace + repository)

Mode is determined at conversation creation based on context parameters:
- `aws_account_id` → ENVIRONMENT_SETUP
- `workspace_id` AND `repo_id` → APP_DEPLOYMENT
- Otherwise → GENERAL

**System prompt split:**

Created three focused prompts:
- `system_prompt_general.md` (~70 lines) — Help with existing resources, guide to proper flows for new things
- `system_prompt_environment.md` (~86 lines) — Just environment setup: list domains, user picks, create, poll
- `system_prompt_app_deployment.md` (~141 lines) — Repo analysis, app creation, deployment

Each prompt tells the agent exactly what it CAN'T do and where to direct users for other tasks. This prevents mode confusion.

**Auto-triggering with SYSTEM_TRIGGER:**

For ENVIRONMENT_SETUP and APP_DEPLOYMENT modes, the agent starts immediately when the conversation opens — no need for the user to type anything. Implemented via:

1. New `SYSTEM_TRIGGER` content type for Message (hidden from UI)
2. `chat_new` creates a trigger message for these modes with friendly content:
   - "Hi! I'd like to set up a new environment in my AWS account. Can you help me get started?"
   - "Hi! I'd like to deploy this repository. Can you help me get it running?"
3. Template filters out SYSTEM_TRIGGER messages from display
4. Agent runner sees the USER role message and starts processing
5. SDK session stores the trigger; our DB hides it — clean separation

The friendly message content sets a warm tone so the agent responds helpfully.

**Model selection by mode:**

Environment setup is simple (list domains, create, poll) so it uses Sonnet (~5x cheaper, faster). App deployment needs complex reasoning (repo analysis, infrastructure decisions) so it uses Opus. General uses Opus.

**Key design discussions:**

- **Inferring mode vs explicit field** — Initially considered inferring mode from which context FKs are set. Decided explicit `mode` field is cleaner: single source of truth, simpler queries, easy to extend. The context FKs remain for relationships and display.

- **Context fields are orthogonal** — `context_aws_account` is mutually exclusive with `context_workspace`/`context_repository`. They represent different conversation intents. UI reflects this with `{% elif %}` patterns.

- **Session continuity** — SDK maintains full conversation history including trigger message. Our DB is a display-friendly mirror that omits implementation details. This works because SDK is the source of truth for Claude's context.

**Key points:**

- Mode-specific prompts are dramatically simpler and more focused than the monolithic approach
- Auto-triggering removes friction — user clicks "New Environment" and agent immediately presents domain options
- SYSTEM_TRIGGER messages exist in SDK session but hidden from UI, preserving clean user experience
- Sonnet for simple flows, Opus for complex reasoning — cost optimization without sacrificing quality

## 2026-01-30 15:30 - [UI] Add Environments section to site navigation

**Conversation:** [2026-01-30-1416-4855039b.md](conversations/2026-01-30-1416-4855039b.md)

Implemented the "Environments" section as a new top-level page in the application. Environments are deployment targets (VPC + ECS cluster) that live within connected AWS accounts. The relationship chain is Organization → AWSAccount → Environment.

**Design decisions:**

- **Navigation placement** — Added "Environments" between "Workspaces" and "Security" in the sidebar, using the Heroicons "server-stack" icon. This groups deployment-related concepts together (Workspaces define apps, Environments define where they run).

- **List page layout** — Follows the same grid pattern as workspace_detail apps: left-to-right boxes showing environment name, AWS account name, status badge, and region. The description explicitly mentions AWS accounts: "Deployment targets within your connected AWS accounts."

- **"New Environment" flow** — Rather than a form, clicking "New Environment" opens a modal to select an AWS account, then redirects to the chat agent with that account as context. This matches our agent-first approach where the AI guides users through environment creation (asking about name, region, domain via Route53).

- **Empty state UX** — When no environments exist, the empty state checks if AWS accounts are connected. If not, it shows a link to Settings → AWS Accounts. This guides users through the prerequisite step.

- **Environment detail page** — Shows environment metadata (AWS account, region, VPC ID, domain) and lists recent deployments. Includes breadcrumb navigation back to the environments list.

**Model change:**

Added `context_aws_account` FK to Conversation model, following the existing pattern for `context_workspace` and `context_repository`. The `chat_new` view now accepts `?aws_account=<uuid>` to set this context. Note: The agent service doesn't yet read this context — that's a follow-up task to wire up the prompt injection and flow guidance.

**Key points:**

- Environment belongs to AWSAccount (not Organization directly), so queries use `aws_account__organization` for filtering
- The "New Environment" button only appears when connected AWS accounts exist
- Agent context is stored but not yet used — follow-up needed to update agent_service.py and system_prompt.md

## 2026-01-29 22:45 - [ControlPlane] Enable psycopg3 native connection pooling for ASGI

**Conversation:** [2026-01-29-2246-3fe2d24d.md](conversations/2026-01-29-2246-3fe2d24d.md)

Following up on the database connection leak fix from earlier, enabled Django 6.0's native connection pooling via psycopg3's `ConnectionPool` instead of relying on `CONN_MAX_AGE`.

**Background:**

The earlier fix (2026-01-30 06:15) addressed connection leaks by adding explicit `connections.close_all()` in async contexts that escape Django's request lifecycle. As a mitigation, `conn_max_age` was set to 0 (close after each request). This worked but sacrificed the latency benefits of connection reuse.

**The better solution — psycopg3 native pooling:**

Django 6.0 added support for psycopg3's built-in `ConnectionPool` via the `pool` option. The Django docs explicitly recommend this approach for ASGI:

> When using ASGI, persistent connections should be disabled. Instead, use your database backend's built-in connection pooling if available.

**Key differences between CONN_MAX_AGE and pool=True:**

- **`CONN_MAX_AGE`** — Per-thread persistent connections. Each thread keeps its own connection. Breaks with ASGI because async contexts create connections outside the threadpool, leading to leaks.

- **`pool=True`** — Uses psycopg3's `ConnectionPool`. A shared pool where connections are borrowed and returned. Designed to work correctly with async code.

**Changes made:**

1. Added `pool` extra to psycopg: `psycopg[binary,pool]>=3.2.0` in pyproject.toml
2. Added `OPTIONS: {"pool": True}` to database config in settings.py
3. Kept `conn_max_age=0` alongside pool (disables Django's per-thread persistence, lets pool handle everything)

**Important: `close_all()` is still required:**

Even with connection pooling, the explicit `connections.close_all()` calls in `chat.py` and `agent_runner.py` remain necessary. The difference is what they do:

- Without pool: `close_all()` closes actual TCP connections
- With pool: `close_all()` returns borrowed connections to the pool

If we don't call `close_all()` in async contexts that escape Django's lifecycle, connections stay "checked out" from the pool's perspective, eventually exhausting the pool.

**Key points:**

- psycopg3's pool is the recommended approach for Django ASGI applications
- `close_all()` changes from "close connections" to "return to pool" but is still required
- Updated comments in both cleanup locations to explain this behavior and cross-reference each other

## 2026-01-30 10:45 - [Bugfix] SSE keepalive to prevent CloudFront timeout disconnections

**Conversation:** [2026-01-29-2236-05c53325.md](conversations/2026-01-29-2236-05c53325.md)

Diagnosed and fixed production SSE connection errors where the browser console showed thousands of `net::ERR_HTTP2_PROTOCOL_ERROR` errors on the `/chat/.../stream/` endpoint, with htmx-ext-sse continuously reconnecting.

**Root cause:**

The architecture is: `Browser (HTTP/2) → CloudFront → ALB → ECS`. CloudFront has a default origin response timeout of 30 seconds. When the SSE stream goes idle (waiting for user input or agent processing), no data flows. After 30 seconds, CloudFront closes the connection. With HTTP/2, this manifests as a `RST_STREAM` frame, which Chrome reports as `ERR_HTTP2_PROTOCOL_ERROR`. htmx-ext-sse auto-reconnects, creating the same timeout cycle, resulting in 2000+ errors.

**Clarification on HTTP/2 + SSE:**

HTTP/2 and SSE are fully compatible — SSE simply becomes one of HTTP/2's multiplexed streams. The issue isn't protocol incompatibility but idle timeout configuration. The same timeout problem would occur with HTTP/1.1, just with a different error presentation (connection reset vs protocol error).

**The fix:**

Add SSE keepalive comments in `chat.py`'s event generator. SSE comments (lines starting with `:`) are ignored by clients but keep data flowing through the connection:

```python
keepalive_interval = 15.0  # Must be < CloudFront's 30s timeout
while True:
    try:
        event = await asyncio.wait_for(runner.event_queue.get(), timeout=keepalive_interval)
        # ... handle event ...
    except asyncio.TimeoutError:
        yield ": keepalive\n\n"  # SSE comment keeps connection alive
```

**Key points:**

- 15-second interval is safely under CloudFront's 30-second default and ALB's 60-second default
- Keepalive logic belongs in the SSE transport layer (`chat.py`), not the agent runner — it's a transport concern
- No frontend changes needed — htmx-ext-sse (and all SSE clients) transparently ignore comment lines
- This is standard SSE practice for long-lived connections behind proxies/CDNs

## 2026-01-30 06:15 - [Bugfix] Fix Django ASGI SSE database connection leak

**Conversation:** [2026-01-29-2210-8db8285e.md](conversations/2026-01-29-2210-8db8285e.md)

Diagnosed and fixed a critical production issue where database connections leaked at +1 per SSE request, eventually exhausting Aurora's connection limit (800+ connections overnight from a single user).

**Root cause:**

Django stores database connections using `asgiref.local.Local(thread_critical=True)`, which in async contexts uses `contextvars` for isolation. Two async patterns escape Django's normal request lifecycle cleanup (via `request_finished` signal):

1. **StreamingHttpResponse generators** — Long-lived SSE streams don't "finish" until the client disconnects, and the `request_finished` signal doesn't properly clean up connections for these long-lived responses.

2. **Background tasks via `asyncio.create_task()`** — The agent runner spawns a background task that does many ORM operations. This task runs outside Django's request lifecycle entirely.

**The fix:**

Add explicit `connections.close_all()` in the `finally` block of BOTH async contexts:

```python
# chat.py - SSE generator
async def event_generator():
    try:
        # ... stream events ...
    finally:
        await sync_to_async(connections.close_all, thread_sensitive=True)()

# agent_runner.py - Background task  
async def _run_agent_loop(...):
    try:
        # ... ORM operations in stream_response() ...
    finally:
        await sync_to_async(connections.close_all, thread_sensitive=True)()
```

**Key learnings:**

- Both `close_all()` calls are required — removing either one causes the leak to return
- Query placement (inside vs outside generator) doesn't matter — what matters is cleanup in async contexts that escape Django's lifecycle
- Django ticket #33497 documents that persistent connections don't work with ASGI, but the SSE-specific nuance is less documented
- The `close_all()` must use `sync_to_async(..., thread_sensitive=True)` because Django's connection management is synchronous

**Debugging approach that worked:**

Added connection count logging via `pg_stat_activity` queries at strategic points to trace where connections were created and whether cleanup was effective. This revealed that `close_all()` showed no effect initially because it was running in a different context than where connections were opened.

**Production pattern for Django ASGI SSE endpoints:**

Any async code that escapes Django's request lifecycle needs explicit connection cleanup:
- `StreamingHttpResponse` with async generators
- Background tasks via `asyncio.create_task()`
- Any long-lived async operation that does DB queries

## 2026-01-29 06:55 - [AgentChat] Fix create_datastore tool schema to prevent invalid deployment_mode guesses

**Conversation:** [2026-01-29-0919-aefbdc1b.md](conversations/2026-01-29-0919-aefbdc1b.md)

Diagnosed a production issue where the agent had to call `create_datastore` twice because it guessed the wrong `deployment_mode` value. The agent used `"serverless"` but the valid values are `"aurora_serverless_v2"` or `"aurora_provisioned"`.

**Root cause:**

The `create_datastore` MCP tool used the simple parameter format which only specifies types without constraints:

```python
@tool("create_datastore", "description...", {
    "deployment_mode": str,  # No guidance on valid values
    "engine": str,
})
```

The tool description mentioned "Serverless v2 scaling" but didn't specify the exact parameter value. The agent reasonably guessed `"serverless"` instead of the required `"aurora_serverless_v2"`.

**Fix:**

Converted to JSON Schema format with enum constraints (same format already used by `deploy_app`):

```python
"deployment_mode": {
    "type": "string",
    "enum": ["aurora_serverless_v2", "aurora_provisioned"],
    "description": "aurora_serverless_v2 (recommended) or aurora_provisioned",
},
"engine": {
    "type": "string",
    "enum": ["aurora-postgresql", "aurora-mysql"],
    "description": "Database engine",
},
```

**Key points:**
- JSON Schema format is superior to simple dict format for MCP tools — it supports per-parameter descriptions, enum constraints, and explicit required vs optional fields
- Enum constraints make valid values explicit in the tool schema that Claude sees, preventing guessing
- The `deploy_app` tool already used this format; `create_datastore` was an inconsistency
- Also made `serverless_min_acu` and `serverless_max_acu` optional (not in `required` array) since they have sensible defaults

## 2026-01-29 23:30 - [AgentChat] Fix duplicate tool outputs on SSE reconnection

**Conversation:** [2026-01-28-2224-7e618e6b.md](conversations/2026-01-28-2224-7e618e6b.md)

Diagnosed and fixed a bug where bash tool outputs appeared duplicated or triplicated in the agent chat. During long-running tool executions (like waiting for Aurora database provisioning), users saw the same "Bash: Wait 2 more minutes for DB instance" card appear 3 times.

**Root cause analysis:**

The duplication stemmed from the interaction between SSE reconnection handling and frontend rendering:

1. **Backend replay mechanism** — When SSE reconnects, `mark_client_connected()` in `agent_runner.py` replays all pending `tool_start` events so users see spinners for tools still in progress:
   ```python
   if runner.pending_tools:
       for tool_data in runner.pending_tools.values():
           runner.event_queue.put_nowait(AgentStreamEvent(type="tool_start", data=tool_data))
   ```

2. **Frontend blindly appends** — `handleToolStart()` always appends the tool div to the messages container without checking if it already exists:
   ```javascript
   while (fragment.firstChild) {
       messages.appendChild(fragment.firstChild);  // No deduplication!
   }
   ```

3. **Result** — Each SSE reconnection creates another duplicate. Browser DevTools confirmed multiple `<div id="tool-{same_id}">` elements (invalid HTML with duplicate IDs).

**Why so many reconnections?**

Console showed repeated `ERR_QUIC_PROTOCOL_ERROR` on the SSE stream endpoint. HTTP/3 (QUIC) doesn't handle long-lived SSE connections well — it has aggressive timeout/flow-control that interrupts streaming. Each error triggers EventSource auto-reconnect, which replays tools, which creates duplicates.

**Fix:**

Made `handleToolStart` idempotent by checking if a tool div with that ID already exists before appending:

```javascript
function handleToolStart(data) {
    // ... parse fragment ...
    
    // Skip if tool already exists (SSE reconnection replay)
    const toolDiv = fragment.querySelector('[id^="tool-"]');
    if (toolDiv && document.getElementById(toolDiv.id)) {
        return;
    }
    
    // Append remaining content to messages
    while (fragment.firstChild) {
        messages.appendChild(fragment.firstChild);
    }
}
```

**Key points:**
- SSE reconnections are unavoidable (network blips, tab throttling, QUIC issues) — the UI must handle them gracefully
- The backend's replay mechanism is correct design — it ensures users see in-progress tools after refresh
- The bug was in the frontend not being idempotent for replayed events
- Created `devopshero-3hn` to investigate the QUIC protocol errors separately (potential CloudFront HTTP/3 configuration)

## 2026-01-29 22:15 - [AgentChat] Auto-scroll re-engagement when user scrolls to bottom

**Conversation:** [2026-01-28-2208-bf425239.md](conversations/2026-01-28-2208-bf425239.md)

Improved the chat panel's auto-scroll behavior. Previously, once a user scrolled up (disengaging auto-scroll), the only way to re-engage was clicking the "scroll to bottom" button. Now auto-scroll re-enables automatically when the user scrolls back to the bottom.

**Evolution of the solution:**

1. **Initial approach: `scrollend` event** — Seemed ideal since it fires once when scrolling stops, avoiding the performance cost of continuous `scroll` events. However, two problems emerged:
   - Doesn't fire if no actual scrolling occurs (e.g., wheeling down when already at bottom)
   - Waits for momentum/inertia to stop — too slow for responsive UX

2. **Consulted GPT-5.2** — Suggested `scroll` with rAF throttling or IntersectionObserver sentinel. Valid for complex cases, but overkill here.

3. **Final solution: Simple `scroll` event** — Check if at bottom on each scroll; re-enable immediately when threshold is reached. No state tracking needed.

**Key fixes:**

- **Wheel direction filtering** — Only disable auto-scroll on `wheel` up (`deltaY < 0`). Wheeling down while at bottom no longer disengages. The `scroll` event doesn't have delta info, but we don't need it — being at bottom is the signal regardless of how you got there.

- **Immediate re-engagement** — Using `scroll` instead of `scrollend` means auto-scroll re-enables the instant you hit bottom, not after momentum stops.

- **Minimal threshold** — 1px handles subpixel rounding across browsers/zoom levels while being effectively "at bottom".

**Final implementation:**
```javascript
// Re-enable auto-scroll when user reaches bottom
container.addEventListener('scroll', function() {
    if (!autoScrollEnabled) {
        const threshold = 1;
        const isAtBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - threshold;
        if (isAtBottom) {
            autoScrollEnabled = true;
            hideScrollButton();
        }
    }
});

// Disable auto-scroll when user scrolls up (away from bottom)
container.addEventListener('wheel', function(e) {
    if (e.deltaY < 0) disableAutoScroll();
});
```

**Key points:**
- `scrollend` is elegant but not suitable for immediate feedback — fires after momentum stops, not during scrolling
- `scroll` event doesn't include delta; direction must be computed from position changes OR handled via the `wheel` event separately
- For re-enabling auto-scroll, direction tracking is unnecessary — if you're at bottom, that's the signal
- Small threshold (1-5px) handles floating-point rounding issues across browsers and zoom levels

## 2026-01-29 19:45 - [Deployment] Aurora PostgreSQL version 15.4 unavailable

**Conversation:** [2026-01-28-1951-3ebc2269.md](conversations/2026-01-28-1951-3ebc2269.md)

Diagnosed a production deployment failure where the agent couldn't create Aurora PostgreSQL databases for customer apps. The error was: `"Cannot find version 15.4 for aurora-postgresql"`.

**Root cause:**
The `deploy_app.py` had hardcoded Aurora PostgreSQL version `15.4` as the default, with a version map containing only that single entry:
```python
AURORA_POSTGRES_DEFAULT_VERSION = "15.4"
AURORA_POSTGRES_VERSION_MAP = {
    "15.4": rds.AuroraPostgresEngineVersion.VER_15_4,
}
```

While the CDK constant `VER_15_4` exists, AWS had retired or made this specific Aurora version unavailable in the region. The control plane's own database uses `VER_16_4` which works fine.

**Fix:**
1. Updated default to `16.4` (matches working control plane)
2. Updated Aurora MySQL default to `3.08.0` (was `3.04.0`)
3. Removed the version maps entirely — now uses CDK's `of()` method which accepts any valid version string:
```python
version = rds.AuroraPostgresEngineVersion.of(
    aurora_postgres_full_version=version_str,
    aurora_postgres_major_version=major,
)
```

This is simpler and future-proof: sensible defaults that work, and any user-specified version passes through to AWS for validation at deployment time.

**DevEx improvements while debugging:**
Querying the conversation logs from production was difficult — I initially guessed wrong field names and the `-field` descending order syntax failed through shell layers. Added three improvements to `doh_query`:

1. **`--describe` flag** — Shows available fields without running a query:
   ```bash
   ./prod_manage.sh doh_query Message --describe
   # Output: Fields: content, content_type, conversation, created_at, id, metadata, role
   ```

2. **`--desc` flag** — Explicit descending order (avoids `-field` syntax that gets mangled):
   ```bash
   ./prod_manage.sh doh_query Message --order created_at --desc
   ```

3. **Conversation query examples** — Added ready-to-use examples in the `prod-manage` skill for the common case of retrieving conversation messages.

**Key points:**
- Aurora engine versions can become unavailable; don't hardcode them
- CDK's `of()` method provides flexibility without maintaining version maps
- The control plane config (VER_16_4) was a working reference we should have matched
- Shell quoting through ECS execute-command remains fragile; purpose-built flags (`--desc`) avoid the problem

## 2026-01-29 19:00 - [Bugfix] Agent fails when repo default branch is not "main"

**Conversation:** [2026-01-28-1852-34bac04d.md](conversations/2026-01-28-1852-34bac04d.md)

Diagnosed production errors where conversations with certain GitHub repositories failed immediately with "Agent task failed unexpectedly". The root cause was a hardcoded `"main"` branch in `agent_service.py` when cloning the repository for conversation context.

**Root cause:**
When a conversation starts with a repository selected, `stream_response()` clones the repo to provide file context to the agent. Line 421 had:
```python
repo_path = await asyncio.to_thread(
    repo_service.clone_repository,
    repository,
    "main",  # TODO: Allow branch selection from context  <-- BUG
    f"conv-{conversation.id}",
)
```

The repository `vmendi/ai-detector-and-humanizer` uses `master` as its default branch (not `main`), causing `git clone --branch main` to fail with "Remote branch main not found in upstream origin".

**Fix:**
Changed to use `repository.default_branch` which is properly synced from GitHub during repository import. The `default_branch` field existed and had correct values — it just wasn't being used here.

**Diagnosing the shell quoting problem:**
Verifying the fix required querying production to check the `default_branch` value. This took many failed attempts because `prod_manage.sh shell -c "..."` mangles quotes through multiple shell layers (local → AWS CLI → ECS → bash → Python). Complex Python with string literals is nearly impossible to pass.

**Solution — new `doh_query` command:**
Created `doh_query` management command for ad-hoc model inspection without quoting issues:
```bash
./prod_manage.sh doh_query Repository full_name default_branch --filter full_name__icontains=ai-detector
```

Features:
- Query any model by name
- Specify fields to display
- Filter with Django ORM syntax (`--filter key=value`)
- Limit and order results
- Shows available models on error

Updated the `prod-manage` skill to document `doh_query` and warn against using `shell -c` for complex Python.

**Key points:**
- The `Repository.default_branch` field was correctly synced from GitHub — the bug was simply not using it
- Shell quoting through ECS execute-command is fragile; purpose-built commands avoid the problem entirely
- `doh_query` covers 90% of debugging needs (inspecting model data) without quoting issues

## 2026-01-29 18:15 - [Integrations] Rename services/github to services/gitproviders

**Conversation:** [2026-01-28-1833-3e29ca4b.md](conversations/2026-01-28-1833-3e29ca4b.md)

Renamed the `devopshero_app/services/github/` directory to `devopshero_app/services/gitproviders/` in preparation for supporting multiple git providers. The existing code handles GitHub App integration (OAuth flow, installation tokens, repository sync, cloning), and the new naming reflects that this module will be the home for all git provider integrations.

**Changes made:**
- `git mv devopshero_app/services/github devopshero_app/services/gitproviders`
- Updated 5 import statements across the codebase:
  - `services/agent/agent_service.py` — imports `repo_service` for cloning repos into agent sandbox
  - `services/agent/mcp_tools.py` — imports `repo_service` for MCP tool repo access
  - `services/deployment/deployment_executor.py` — imports `repo_service` for deployment cloning
  - `views/settings.py` — imports `github_client` for re-sync functionality
  - `views/github.py` — imports `github_client` for OAuth flow

**Architecture context:**
The existing GitHub integration consists of:
- `github_client.py` — JWT generation for GitHub App auth, installation token exchange, repo listing, sync logic
- `repo_service.py` — Cloning with installation tokens (`https://x-access-token:{token}@github.com/...`), handles local `file://` URLs for dev
- `views/github.py` — OAuth flow (`/github/connect`, `/github/callback`, webhook endpoint)
- Models already have `Provider.GITHUB` and `Provider.GITLAB` enum values in `GitProviderIntegration` and `Repository`

**Key points:**
- The `repo_service.py` name remains appropriate since it handles cloning for any provider
- Provider-specific API clients (like `github_client.py`) will be siblings in this directory
- No functional changes — purely a rename for better organization

## 2026-01-29 16:45 - [UI] Landing page header logo update to shield icon

**Conversation:** [2026-01-28-1802-a5d820f5.md](conversations/2026-01-28-1802-a5d820f5.md)

Updated the landing page header to use the actual DevOps Hero shield logo instead of a generic Zap icon SVG. The shield logo (`devops-hero-logo-shield.png`) already existed in the static assets and is used in the dashboard sidebar — this change brings visual consistency between the landing page and the authenticated app experience.

**Changes made:**
- Replaced the inline SVG Zap icon (wrapped in a gradient container with hover effects) with the shield PNG image
- Added `{% load static %}` to the header partial template
- Set initial height to `h-10` but bumped to `h-14` after visual testing showed it was too small relative to the "DevOpsHero" text
- Kept the hover scale animation (`group-hover:scale-110`) for interactivity

**Key points:**
- Brand consistency matters — using the same shield logo across landing and dashboard reinforces visual identity
- The shield PNG has a transparent background and includes the infinity symbol, blue shield, and swoosh, which is more distinctive than a generic icon
- Size tuning required iteration: the previous container was 40px (`w-10 h-10`) but the shield needed 56px (`h-14`) to balance visually with the text

## 2026-01-29 15:32 - [UI] Landing page "vibe-deploying" messaging pivot

**Conversation:** [2026-01-28-1759-e700af23.md](conversations/2026-01-28-1759-e700af23.md)

Reworked the landing page hero badge to embrace the "vibe-coding → vibe-deploying" narrative. The original tagline "Heroku-style deployments inside your AWS account" was functional but didn't capture the cultural moment — AI has made building apps dramatically faster (vibe-coding), and deployment is now the bottleneck.

**Hero badge changes:**
- Changed copy from "Heroku-style deployments inside your AWS account" to "Everyone's vibe-coding. Now everyone can vibe-deploy."
- Made the badge more prominent: `text-sm` → `text-lg`, `font-medium` → `font-semibold`, increased padding (`px-4 py-2` → `px-6 py-3`), larger icon (`w-4 h-4` → `w-5 h-5`)
- The "everyone" framing emphasizes democratization — it's not just for the individual developer, it's about enabling the whole team/company

**Solution section cleanup:**
- Kept the title "The Heroku experience inside your enterprise" — tried "The vibe-deploying experience" but the longer text caused awkward line breaks
- Removed redundant opening sentence "DevOps Hero brings the Heroku, Render or Railway experience inside your enterprise" from the paragraph since the headline already conveys this

**Key points:**
- "Vibe-deploying" positions DOH as the natural next step after vibe-coding tools like Cursor/Claude/Copilot
- Using "everyone" instead of "you" shifts focus from individual to team empowerment
- Heroku remains a useful reference for the solution section since it's universally understood shorthand for "easy deployment"

## 2026-01-29 11:45 - [UI] Landing page full-page scroll and layout improvements

**Conversation:** [2026-01-28-1741-29f25199.md](conversations/2026-01-28-1741-29f25199.md)

Implemented full-page scroll snapping for the landing page, where each section fills the viewport and scrolling moves between sections smoothly. Also made various layout and styling improvements.

**Full-page scroll implementation:**

Initially tried CSS `scroll-snap-type: y mandatory` on the body element, but this didn't work — browsers use the `<html>` element as the viewport scroll container. Moving the snap properties to `<html>` worked for keyboard navigation (PageDown) but not for mouse wheel on Mac due to trackpad inertia fighting with mandatory snapping.

The solution was JavaScript-based section scrolling:
- Listen for `wheel` events with `{ passive: false }` to allow `preventDefault()`
- Accumulate wheel delta until it passes a threshold (15px) to filter micro-movements
- Call `scrollIntoView({ behavior: 'smooth' })` on the target section
- Block additional scrolls during animation (800ms) to prevent jitter
- Reset accumulator after 150ms pause in scrolling

**Layout changes:**

- **Logo navigation** — Made logo link to root `/` without HTMX (full page navigation to landing)
- **Removed slide-up animations** — Removed `animate-slide-up` from hero section elements for cleaner appearance
- **Header styling** — Removed dynamic scroll effect that toggled background/border classes; now permanently styled with `bg-transparent backdrop-blur-xl border-b border-white/[0.06]`
- **Problem section** — Removed `section-padding` class (was adding 128px padding) and replaced with `pt-24 pb-16` for tighter spacing; reduced margins around stats box
- **CTA section** — Reduced padding from `section-padding` (128px) to `pt-16 pb-8`
- **Footer as separate section** — Made footer its own `landing-section` with `min-h-screen flex items-end` so it scrolls to separately and aligns content to bottom of viewport

**Key points:**

- CSS scroll-snap doesn't work well with Mac trackpad inertia — JavaScript provides better control
- Each landing section needs `landing-section` class for the JS scroll handler to find them
- Footer uses `items-end` instead of `items-center` to align content to viewport bottom
- Sections with content taller than viewport need careful padding to fit; debug with browser tools checking actual heights vs viewport

## 2026-01-28 22:35 - [ControlPlane] Add devopshero.co redirect to devopshero.ai

**Conversation:** [2026-01-28-1419-d25d7b1e.md](conversations/2026-01-28-1419-d25d7b1e.md)

Added infrastructure to redirect the secondary domain `devopshero.co` (apex and www) to the primary domain `devopshero.ai`. This ensures users who land on the .co domain are redirected to the canonical .ai domain.

**Implementation approach:**

Created a new `RedirectStack` CDK stack that uses a CloudFront Function to return 302 (temporary) redirects. The stack creates:

- ACM certificate for `devopshero.co` and `www.devopshero.co` with DNS validation
- CloudFront Function that intercepts all requests and returns 302 redirects preserving the URI path
- CloudFront distribution with the function attached
- Route53 A records (Alias) for both apex and www pointing to CloudFront

**Why 302 instead of 301:**

Chose 302 (temporary redirect) over 301 (permanent) to avoid browser caching issues. With 301, browsers cache the redirect indefinitely, making it difficult to change later. 302 gives flexibility to modify the redirect behavior without users having stale cached redirects.

**Why CloudFront Function instead of S3:**

Explored alternatives including S3 website hosting with built-in redirect rules. However, S3's `website_redirect` feature only supports 301 redirects — there's no option for 302. For temporary redirects, CloudFront Functions are the simplest AWS approach despite requiring a small JavaScript snippet. Lambda@Edge would work but is overkill for this use case.

**Deployment gotcha:**

After creating the stack, running `deploy.sh` didn't deploy it because `deploy.sh` has a hardcoded list of stacks. The redirect stack was added to `app.py` but `deploy.sh` wasn't updated. Diagnosed this when `devopshero.co` wasn't loading — Route53 showed no A record for apex (only an old wildcard pointing to a defunct ALB). Fixed by adding step 7/7 to `deploy.sh`.

**Key points:**

- New `redirect_stack.py` creates all resources needed for the redirect
- CloudFront Function is 8 lines of JS that returns 302 with `Location: https://devopshero.ai{original_path}`
- Requires hosted zone for `devopshero.co` to exist in Route53 before deployment
- **Important:** When adding new CDK stacks, remember to update `deploy.sh` — it doesn't auto-discover stacks

## 2026-01-28 22:10 - [DevEx] Move extract_resources.py to infra_devopshero

**Conversation:** [2026-01-28-1338-b18ae424.md](conversations/2026-01-28-1338-b18ae424.md)

Relocated the `extract_resources.py` script from `.claude/skills/debug-production/` to `infra_devopshero/`. This script parses CDK source files to extract production resource names (cluster, service, log groups, etc.) for debugging purposes.

**Rationale:**

The script belongs with the infrastructure code it analyzes, not buried in a skill folder. Placing it in `infra_devopshero/` makes it discoverable alongside other infrastructure tooling and follows the principle of co-locating related code. The debug-production skill now references the new location.

**Key points:**

- Script moved from `.claude/skills/debug-production/extract_resources.py` to `infra_devopshero/extract_resources.py`
- Updated skill reference to point to new location
- Script logic unchanged — it still walks up to find `infra_devopshero/` which now resolves immediately

## 2026-01-28 21:45 - [Bugfix] Investigating Production Database Connection Exhaustion

**Conversation:** [2026-01-28-1336-e98d63ee.md](conversations/2026-01-28-1336-e98d63ee.md)

PostHog showed production errors on the chat streaming endpoint (`/chat/.../stream/`):

1. `OperationalError: connection failed ... FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute`
2. `AttributeError: 'SessionStore' object has no attribute '_session_cache'`

**Initial diagnosis pointed to connection exhaustion:**

- Aurora Serverless v2 at `min_capacity=0.5` ACU provides only ~45 connections when idle
- Django `conn_max_age=60` kept connections alive for 60 seconds
- The async agent runner polls the database every 500ms while clients are connected
- Long-running SSE streams could hold connections for extended periods

The SessionStore error appeared to be a cascading failure — when Django's session middleware couldn't get a database connection, it failed with an AttributeError.

**Mitigation applied:**

Changed `conn_max_age` from 60 to 0 in `settings.py`. This closes database connections after each request instead of keeping them alive. Trade-off is slightly higher latency per request, but prevents connection pool exhaustion.

```python
# Before
DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}

# After  
DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=0)}
```

**Important caveat:** We're not 100% certain this is the root cause. The comment reflects this uncertainty — we need to monitor after deployment to confirm.

**Logging gap discovered:**

CloudWatch logs showed HTTP 500 responses but no stack traces. The logging configuration only captures `devopshero_app` logs, not Django exceptions or the root logger. Exceptions are captured by PostHog middleware but not printed to CloudWatch. This should be addressed separately to improve production debugging.

**Alternative solutions considered (not implemented):**

- Increase Aurora `min_capacity` to 2 ACU (~180 connections) — costs more
- Add PgBouncer connection pooling — more infrastructure
- Reduce agent runner polling frequency — changes behavior

## 2026-01-28 14:15 - [AgentChat] Making deploy_app Tool Optional Parameters Actually Optional

**Conversation:** [2026-01-28-1330-bd79e017.md](conversations/2026-01-28-1330-bd79e017.md)

The DOH agent was failing when trying to deploy apps without a database. The agent would either pass `"datastore_id": "null"` (string literal) which failed UUID validation, or omit the field entirely which failed because it was marked as required. This was a schema definition problem in the MCP tool.

**Root cause discovered:**

The claude-agent-sdk `@tool` decorator has two schema formats:

1. **Simple type mapping** — `{"name": str, "datastore_id": str}` — ALL fields become required
2. **Full JSON Schema** — `{"type": "object", "properties": {...}, "required": [...]}` — explicit control

Our `deploy_app` tool used the simple format, so every field was required. The SDK source code confirmed this:

```python
# In create_sdk_mcp_server when processing simple dict schemas:
schema = {
    "type": "object",
    "properties": properties,
    "required": list(properties.keys()),  # ALL properties marked required!
}
```

**Solution:**

Converted `deploy_app` to use full JSON Schema format with explicit `"required"` array. Made these fields optional:

- `datastore_id` — apps without databases can omit this entirely
- `dockerfile_path` — not always needed
- `environment_variables` — omit to keep existing values
- `app_secrets` — omit to keep existing values
- `git_ref` — defaults to HEAD of branch (most common case)
- `branch` — defaults to `repository.default_branch` (we already fetch the repo)

**Key insight:** Since we already fetch the Repository in deploy_app to validate it, we can use `repository.default_branch` as the default. Similarly, `git_ref` can default to the branch value. This reduces cognitive load on the agent.

**Future improvements identified (not implemented):**
- Default `environment_slug` to "default" (the description says "always use 'default'")
- Default `app_type` to "web" (most common)
- Default `cpu` to 256 and `memory` to 512 (sensible starting points)
- Default `name` to `repository.name`

These would reduce truly required fields from 8 to just 3: `build_strategy`, `container_port`, `health_check_path` — all of which come from `scan_repository`.

## 2026-01-28 13:25 - [DevEx] Cursor Conversation Extraction Script for Journal Linking

**Conversation:** [2026-01-28-1322-a14d52b0.md](conversations/2026-01-28-1322-a14d52b0.md)

Created a script to extract Cursor conversations to markdown files, enabling journal entries to link back to the conversation that generated them. This provides traceability between documented decisions and the full context of the discussion.

**The problem:** Journal entries capture decisions and reasoning, but sometimes you want to revisit the full conversation that led to those decisions. Cursor stores conversations internally in an SQLite database, not as accessible files.

**Investigation findings:**

Cursor stores all conversation data in `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`. Conversations are keyed as `composerData:<UUID>` and contain JSON with the full message history. The `CURSOR_TRACE_ID` environment variable exists but is unrelated to conversation UUIDs — it's a separate tracing mechanism.

Key limitation discovered: in-progress conversations aren't visible in the database until they're saved/closed. This means you can't extract the current conversation while it's happening — extraction must happen after.

**Solution implemented:**

Created `.claude/skills/journal/extract_conversation.py` with these capabilities:
- `--list` — Show recent conversations sorted by content size (conversations with actual content appear first)
- `--search "keyword"` — Filter by first user message
- `--search "keyword" --full` — Full-text search across all messages
- `--uuid <UUID>` — Extract specific conversation to markdown
- Default output to `docs/conversations/` (gitignored since these files are large)

**Key points:**
- Conversations stored in SQLite at `state.vscdb`, not individual files
- UUIDs identify conversations: `composerData:<UUID>`
- Full-text search required iterating all messages, not just first user message
- Added `.gitignore` in `docs/conversations/` to exclude extracted files from version control
- Updated journal skill with instructions for linking conversations post-session

---

## 2026-01-28 21:35 - [ControlPlane] CloudFront Origin Request Policy: all() Forwards Host Header

Attempted to improve the PostHog proxy by forwarding more headers to PostHog for better analytics enrichment. Changed from `OriginRequestHeaderBehavior.allow_list("Origin")` to `OriginRequestHeaderBehavior.all()`. This immediately broke the proxy with 502 Bad Gateway errors.

**Root cause:** `all()` forwards ALL viewer headers including the `Host` header. When CloudFront sends `Host: devopshero.ai` to `us-assets.i.posthog.com`, PostHog's server rejects the request because the Host doesn't match their expected domain.

**The fix:** Reverted to `allow_list("Origin")`. CloudFront automatically sets the correct `Host` header for the origin when you don't explicitly forward it.

**Why the original config was actually fine:**

Initial concern was that not forwarding headers like `User-Agent`, `Content-Type`, and `Accept-Language` would reduce analytics quality. After verifying in PostHog's dashboard, all device/browser/OS data was present. The reason: PostHog's JavaScript SDK captures all this data **client-side** and embeds it in the JSON request body — it doesn't rely on HTTP headers for device detection. The SDK calls `navigator.userAgent`, reads screen dimensions, etc., and includes them as event properties.

**Key points:**
- `OriginRequestHeaderBehavior.all()` includes `Host`, which breaks reverse proxies
- For reverse proxies, always use `allow_list()` with specific headers (excluding `Host`)
- PostHog SDK is self-sufficient — it captures device info client-side, headers aren't needed for enrichment
- When in doubt, check the actual data in the destination service before "fixing" header forwarding

---

## 2026-01-27 22:45 - [AgentChat] Streaming Message Placeholder Minimum Height

Fixed visual jump when assistant messages start streaming. The `#streaming-text` div in `_streaming_start.html` was initially empty, causing it to collapse to zero height. When the first text chunk arrived, the container would suddenly expand, creating a jarring visual shift.

**Solution:** Added `min-h-[1.5em]` to the `#streaming-text` div. Using `1.5em` (relative to font size) ensures the placeholder matches one line of text regardless of the actual font size applied. This way the message bubble appears at its correct minimum height immediately when inserted, and text streams in without layout shifts.

---

## 2026-01-28 21:17 - [ControlPlane] PostHog CloudFront Reverse Proxy

Implemented a reverse proxy for PostHog analytics through our existing CloudFront distribution to bypass ad blockers. Many browser extensions block requests to `posthog.com` domains, causing lost analytics data. Routing through our own domain makes PostHog traffic appear as first-party.

**Architecture approach:**

Considered two options: (1) separate CloudFront distribution for a subdomain like `ph.devopshero.ai`, or (2) path-based routing through existing distribution. Chose path-based because CloudFront doesn't route behaviors by Host header, so a subdomain would require a separate distribution anyway. Path prefix also avoids SSL certificate complexity.

**CloudFront configuration (`cdn_stack.py`):**

1. **Two new origins** — `us.i.posthog.com` for API requests (event capture, feature flags) and `us-assets.i.posthog.com` for static assets (PostHog SDK JavaScript).

2. **Non-obvious path prefixes** — Used `/doh-ph/*` and `/doh-ph-static/*` instead of obvious names like `/analytics` or `/posthog`. PostHog docs explicitly warn that ad blockers catch common patterns.

3. **CloudFront Functions for path rewriting** — The proxy paths need to be stripped before forwarding to PostHog. Created two functions: one strips `/doh-ph` prefix, the other rewrites `/doh-ph-static/*` to `/static/*` (PostHog serves assets from `/static/`).

4. **Cache policy with minimal TTL** — CloudFront has a frustrating restriction: `Authorization` header can only be forwarded via cache policy, not origin request policy. But cache policies with TTL=0 can't specify headers. Workaround: use 1-second TTL cache policy that includes `Authorization` and `Origin` headers.

**Key CloudFront learnings:**

- **Header forwarding restrictions** — `Authorization` and `Accept-Encoding` MUST use cache policy, cannot be in origin request policy. First deployment failed with cryptic "Invalid request" error.
- **Caching disabled = no headers** — When using `CACHING_DISABLED`, you cannot specify which headers to forward. The policy implicitly forwards nothing. Had to switch to minimal-TTL cache policy.
- **CloudFront Functions vs Lambda@Edge** — Functions are cheaper and faster (sub-millisecond) but limited to request/response modification. Perfect for our simple path rewriting use case.

**Application changes:**

- **`context_processors.py`** — Added `POSTHOG_PROXY_HOST` support. When set, outputs `useProxy: true` and the proxy URL in the JSON config. Frontend uses this to determine SDK loading strategy.
- **`_posthog.html`** — Modified the PostHog snippet to detect proxy mode. The standard snippet loads SDK from `api_host.replace(".i.posthog.com", "-assets.i.posthog.com")+"/static/array.js"` — this replacement doesn't match our proxy URL, so we override to load from `/doh-ph-static/array.js` instead.
- **`apps.py`** — Python SDK backend uses proxy URL when `POSTHOG_PROXY_HOST` is set.
- **ECS task** — Added `POSTHOG_PROXY_HOST` to secrets injection in `app_stack.py`.

**Opt-in design:**

The proxy is only activated when `POSTHOG_PROXY_HOST` env var is set. Without it, everything works exactly as before (direct to PostHog). This allows local development to skip the proxy and simplifies debugging.

**Key points:**
- PostHog warns self-hosted proxies lose their support — debugging issues becomes harder
- AWS WAF (if enabled later) needs 64MB body size limit for session recordings (default is 8KB)
- `ui_host` must point to `https://us.posthog.com` so toolbar and dashboard links work correctly

---

## 2026-01-28 22:15 - [UI] Default to Dark Mode

Users were seeing light theme by default because Tailwind CSS v4 uses `prefers-color-scheme` media query, which follows the OS preference. Most users have light mode set in their OS, resulting in a light UI despite having dark mode styles throughout the codebase.

**Solution: Class-based dark mode**

Instead of relying on OS preference, we now use Tailwind's class-based dark mode strategy:

1. **Added custom variant in CSS** — `@custom-variant dark (&:where(.dark, .dark *));` in `styles.css` tells Tailwind to apply `dark:` utilities when an ancestor has the `dark` class, rather than using the `prefers-color-scheme` media query.

2. **Added `dark` class to HTML element** — The base template now has `<html class="... dark">`, forcing dark mode for all users regardless of their OS setting.

This approach was chosen over flipping all color classes (e.g., `bg-gray-900` as default instead of `bg-white dark:bg-gray-900`) because:
- It's a 2-line change vs hundreds of template changes
- It preserves the ability to add a theme toggle later (just toggle the `dark` class)
- The existing `dark:` variant classes work exactly as intended

---

## 2026-01-28 23:15 - [UI] Waitlist Email Capture on Landing Page

Wired up the "Notify me" forms on the landing page to capture email signups in the database for early access notifications.

**Implementation approach:**

1. **New `WaitlistSignup` model** — Simple model with `email` (unique), `source`, and `created_at`. The `source` field tracks which form the user signed up from (hero section at top vs CTA section at bottom) so we can measure conversion rates for each placement.

2. **HTMX for smooth UX** — The landing page is a standalone template that doesn't extend `base.html`, so it didn't have HTMX. Added the HTMX script and converted both forms to use `hx-post` with `hx-target` pointing to a response container. On submit, the helper text swaps to a success message without page reload.

3. **Security consideration for duplicates** — When a duplicate email is submitted, we silently return success rather than revealing "this email already exists." This prevents email enumeration attacks where someone could probe to see which emails are already in the waitlist.

4. **Admin registration** — Added `WaitlistSignupAdmin` with list display showing email, source, and timestamp. Includes filters by source and date for segmentation analysis.

**Key points:**
- Two forms exist: `_hero.html` (above fold) and `_cta.html` (bottom of page) — both now submit to `/waitlist/signup/`
- Source values are `hero` and `cta` respectively, passed via hidden input
- The view is simple POST-only, no authentication required (public landing page)
- Django's `IntegrityError` on duplicate email is caught and handled gracefully

---

## 2026-01-27 19:55 - [UI] Recover Sign-in Button on Landing Page

During the React-to-Django template conversion (see 2026-01-27 15:45 entry), the sign-in button was lost from the landing page header. The old `landing.html` had conditional auth logic that wasn't carried over to the new partials structure.

**What was missing:**

The old view passed `is_authenticated` context explicitly:
```python
context = {
    "is_authenticated": request.user.is_authenticated,
    "site_logo_url": static('devopshero_app/devops-hero-logo-large.png'),
}
```

The new view stripped this to just `return render(request, ...)` with no context.

**Recovery:**

1. Updated `views/landing.py` to pass `is_authenticated` context again
2. Updated `_header.html` with conditional CTA buttons:
   - **Not authenticated**: "Sign in" text link + "Get Early Access" primary button
   - **Authenticated**: "Go to Dashboard" primary button
3. Applied same logic to mobile menu for consistency

The sign-in link uses simple styling (`text-gray-400 hover:text-white`) to differentiate from the primary CTA button, following the common pattern of secondary auth links in marketing headers.

---

## 2026-01-28 21:35 - [Integrations] PostHog Analytics Integration

Integrated PostHog analytics for both frontend (auto-capture) and backend (exception tracking) to understand user behavior and catch production errors.

**Architecture decisions:**

1. **Frontend JS SDK + Backend Python SDK** — The JS SDK auto-captures pageviews, clicks, and session recordings. The Python SDK is primarily for exception tracking with request context. We're NOT using the Python SDK for manual event capture since we don't have backend-specific events worth tracking yet.

2. **Django middleware included** — Added `PosthogContextMiddleware` even though we're not capturing backend events. The middleware provides request context (URL, method, user ID) when exceptions are auto-captured, making debugging significantly easier. Added a request filter to skip `/admin`, `/health`, `/static`, `/__reload__`.

3. **Dev environment exclusion** — The JS snippet checks for localhost, 127.0.0.1, and ngrok.io to avoid polluting analytics during development. This is done client-side rather than server-side so the snippet is still present (easier to debug issues).

4. **Context processor for config** — Created `context_processors.py` to serialize PostHog config as JSON. This avoids mixing Django template tags inside JavaScript code blocks, which caused linter warnings and is generally ugly. The template just outputs `{{ posthog_config_json|safe }}` into a JSON script tag.

5. **Partial for reusability** — Extracted the PostHog snippet to `partials/_posthog.html` since the landing page uses a standalone template that doesn't extend `base.html`. Both templates now include the same partial.

6. **User identification** — Logged-in users are identified with their ID, email, and name. This happens in the context processor so it's automatically available wherever the partial is included.

**Infrastructure changes:**

- Added `devopshero/prod/posthog` secret to AWS Secrets Manager via `sync_secrets.py`
- Updated `app_stack.py` to inject `POSTHOG_API_KEY` and `POSTHOG_HOST` into the ECS task definition
- PostHog SDK v7.7.0 installed (latest stable)

**Key learnings:**

- PostHog Python SDK has no native async support (GitHub issue #103) but the middleware is lightweight enough that it doesn't matter for our async Django app
- The Python SDK does NOT auto-instrument events like the JS SDK — it's primarily for manual capture and exception tracking
- The `enable_exception_autocapture=True` option requires using the `Posthog()` constructor, not the simpler `posthog.api_key = ...` pattern shown in Django docs
- Landing page had its own HTML structure and wasn't getting PostHog until we added the include

## 2026-01-28 19:40 - [Deployment] ECS Stabilization Timeout Race Condition Fix

Debugged a "failed" deployment of simple-dashboard that was actually running fine. Root cause was a race condition between the ECS stabilization timeout and the deployment completing.

**The symptom:**

Deployment logs showed `CDK deployment failed` with message `Service failed to stabilize. Check ECS console for details. Timed out after 180 waiting for service`. However, AWS showed the ECS service running and healthy.

**Investigation timeline:**

Checked CloudFormation (CREATE_COMPLETE), ECS service (1/1 running), target group (healthy), and the app itself (HTTP 200). Everything was fine. The DOH deployment-logs command revealed the truth: the deployment was marked failed at 03:20:25, but AWS events showed the service reached steady state at... 03:20:25. Same moment.

**Root cause analysis:**

The stabilization code in `ecs_utils.py` requires `rollout_state == "COMPLETED"` before considering the service stable. The ECS deployment timeline was:

- 03:17:22 — Started waiting for stabilization (180s timeout)
- 03:17:58 — 1/1 running, but 1 pending (old task still draining)
- 03:19:15 — AWS began draining connections from old task
- 03:20:25 — Rollout completed AND timeout expired at same instant

The 180-second timeout was barely enough. Key contributors:

1. **Health check grace period: 60s (CDK default)** — ECS waits 60 seconds before checking ALB health, even for fast-starting apps like Streamlit that boot in ~1 second.
2. **minimumHealthyPercent: 100%** — Old task can't be killed until new one passes health checks.
3. **ALB healthy threshold: 2 checks × 5s** — Another ~10s after grace period.

Minimum deployment time: ~105-135s, leaving only ~45-75s buffer.

**The fix:**

Added explicit `health_check_grace_period=Duration.seconds(15)` to the FargateService in `deploy_app.py`. This reduces the grace period from 60s (CDK default) to 15s, which is plenty for most containerized apps.

**Key learnings:**

- CDK defaults aren't always suitable — 60s grace period is for slow JVM apps, not modern containers
- Race conditions with exact timing are real — the timeout and completion happened at the same second
- "Failed" deployments may be false positives — always verify AWS state directly
- The deregistration delay was already optimized (5s) — wasn't the culprit despite initial suspicion

## 2026-01-28 17:50 - [AgentChat] Unified deploy_app Tool with Upsert Semantics

Merged `create_app` and `deploy_app` into a single tool to eliminate duplicate app creation when redeploying after teardown.

**The problem:**

When a user tears down an app and asks to "deploy the same app again," the agent was calling `create_app`. Since the App record still exists (teardown only deletes AWS infrastructure, not the DB record), `create_app` would find the slug collision and auto-suffix it: `simple-dashboard` → `simple-dashboard-1`. This created orphan duplicates and confused users.

The tool descriptions told the agent to call `list_apps` first to check for existing apps, but LLMs don't reliably follow multi-step patterns like this.

**Design decision: single unified tool**

Instead of relying on the agent to pick the right tool, we merged them into one `deploy_app` with upsert semantics:
- Look up existing app by `(organization, slug)` where `slug = slugify(name)`
- If exists → update config, create deployment
- If not exists → create app, create deployment

This makes "deploy this thing" a single-intent operation. The agent doesn't need to decide between tools.

**Consulted Codex (GPT-5.2) for external validation.** Key insights that shaped the design:

1. **DB constraint is essential** — The unique constraint on `(organization, slug)` prevents duplicates under race conditions. Application-level checks alone aren't sufficient.

2. **Don't match on repository** — Repository URLs change (renames, monorepos). Match by app identity and *guard* repo changes instead. We error if existing app has different repository.

3. **Sentinel semantics for env/secrets** — Needed clear semantics for "keep unchanged" vs "clear" vs "replace". Settled on:
   - `None` (not provided) → keep existing unchanged
   - `[]` or `{}` (empty) → clear all
   - `[values]` or `{values}` → replace with provided

4. **Return `app_created` flag** — Response indicates whether app was created or updated, so agent can communicate "Created and deployed simple-dashboard" vs "Redeployed simple-dashboard with updated config."

**Key design choices:**

- **Identity is `(organization, slug)`** — Kept at org level (not workspace). This was the existing constraint.
- **`app_type` is config, not identity** — Separate apps should have distinct names like "dashboard-web", "dashboard-worker".
- **Repository mismatch = hard error** — Prevents accidentally rewiring an app to wrong repo.
- **Silent updates for other config** — `cpu`, `memory`, `container_port`, `branch`, `build_strategy`, etc. can change without explicit flags.

**Implementation:**

- Rewrote `deploy_app.py` with full upsert logic, sentinel value handling, and race condition recovery via `IntegrityError` catch-and-retry
- Deleted `create_app.py` entirely — no longer exposed as a tool
- Updated `mcp_tools.py` — removed `create_app`, updated `deploy_app` signature with all config params
- Tool count: 15 → 14

**Cleanup:**

The `_enrich_tool_input()` function in `agent_service.py` was originally added to resolve UUIDs to friendly names for UI display (e.g., show "Deploy App: my-cool-app" instead of a UUID). With the new `deploy_app` taking `name` directly, this enrichment is no longer needed. Kept the function as a hook but updated the docstring to document the history.

---

## 2026-01-28 00:45 - [Deployment] Remote EC2 Docker Builder via SSH-over-SSM

Implemented a remote Docker builder that runs on EC2 in the customer's AWS account, solving the fundamental problem that DOH runs on ECS Fargate which doesn't support Docker-in-Docker. This was a multi-day effort with significant design discussion, implementation, debugging, and iteration.

**The problem:**

DOH needs to build customer Dockerfiles to deploy their apps. When running locally, we use the host's Docker daemon. But in production, DOH runs as a container on ECS Fargate, which doesn't expose a Docker socket. We can't use Docker-in-Docker on Fargate, and we don't want to run privileged containers for security reasons.

**Design alternatives considered:**

1. **S3 for code transfer** — Upload source to S3, have builder pull it. Rejected because it requires a bucket in the customer's account (more moving parts) and adds complexity for a simple file transfer use case.

2. **EFS in customer account** — Shared filesystem between DOH and builder. Rejected for the same reason: we want minimal footprint in customer accounts.

3. **SSH-over-SSM** — Use AWS Systems Manager Session Manager to tunnel SSH to the EC2 instance. No inbound security group rules needed, no bastion hosts, no public IPs. SSH gives us `scp`/`rsync` for file transfer. This won.

**Key design decisions:**

- **One EC2 builder per Environment** — Each Environment gets its own builder instance. EBS volume persists Docker layer cache even when instance stops, so subsequent builds are fast.

- **Auto-shutdown after idle** — A watchdog systemd service monitors `/home/ec2-user/last_build_activity`. If no activity for 15 minutes, the instance shuts itself down. Keeps costs low for infrequent deployments.

- **SSH via EC2 Instance Connect** — Instead of managing SSH keys, we generate ephemeral key pairs per build. Push the public key via EC2 Instance Connect (valid for 60 seconds), then SSH immediately. No long-lived credentials.

- **Private subnet with NAT** — Builder sits in private subnet for security. NAT gateway provides outbound internet for pulling base images and pushing to ECR.

- **ARM64 Graviton (t4g.medium)** — Initially used t3.medium (x86_64), but hit architecture mismatch issues. The app's Dockerfile had `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv` which pulled the builder's native architecture (x86_64), but the resulting image needed to run on ARM64 Fargate. Switching to Graviton means native ARM64 builds — no cross-compilation needed, and multi-stage `COPY --from` pulls correct architecture automatically.

- **Feature flag for local dev** — `DOH_USE_REMOTE_BUILDER=1` env var controls whether to use remote builder. Local development continues using host Docker directly. This keeps the dev experience fast while production uses the remote builder.

**Implementation components:**

- `deploy_base.py` — Added `BuilderStack` CDK construct with EC2 instance, IAM role (SSM + ECR permissions), security group (no inbound), and user data script that installs Docker and the watchdog service.

- `ec2_builder_utils.py` — New module with functions: `get_builder_instance_id()` (find by tag), `ensure_builder_running()` (start if stopped, wait for running), `wait_for_ssm_ready()` (poll until SSM agent online), `transfer_source()` (rsync via SSH-over-SSM), `run_remote_docker_build()` (SSH to run docker build/tag/push).

- `ecr_utils.py` — Modified `build_and_push_docker_image()` to check `DOH_USE_REMOTE_BUILDER` and dispatch to either local or remote build path.

- `infra_devopshero/Dockerfile` — Added dependencies for remote builder: `openssh-client` (for ssh-keygen and ssh), `rsync` (for file transfer), `awscli` (for SSM start-session), and AWS Session Manager Plugin (arm64 .deb).

**Debugging journey:**

This took significant debugging to get working in production:

1. **Missing ssh-keygen** — Container didn't have openssh-client. Added to Dockerfile.

2. **Missing rsync** — Needed for efficient file transfer. Added to Dockerfile.

3. **`aws: command not found` in ProxyCommand** — The AWS CLI was installed in the venv but not on PATH when bash runs ProxyCommand. Fixed by using absolute path `/app/.venv/bin/aws`.

4. **Session Manager Plugin not found** — Required for `aws ssm start-session`. Installed the arm64 .deb package in Dockerfile.

5. **Permission denied on activity file** — Watchdog script tried to write to `/tmp/last_build_activity` but ran as root while SSH user was ec2-user. Changed to `/home/ec2-user/last_build_activity` with proper ownership.

6. **ProxyCommand quoting nightmare** — Environment variables with special characters broke the SSH command. Fixed by using `bash -c 'export VAR=val; command'` pattern and `shlex.quote()` for proper escaping of credentials.

7. **ARM64 architecture mismatch** — The killer bug. `COPY --from=ghcr.io/astral-sh/uv:latest` pulled x86_64 binary because the builder was x86_64, but target was ARM64. Solved by switching to Graviton (t4g.medium) so native builds match target architecture.

**Key learnings:**

- SSH-over-SSM is powerful for secure access without opening inbound ports, but the ProxyCommand setup with credentials requires careful shell escaping.
- Multi-stage Docker builds with `COPY --from` don't respect `--platform` flag — they pull based on builder's native architecture. Match builder architecture to target to avoid issues.
- Graviton instances are cheaper and eliminate cross-compilation complexity when targeting ARM64 Fargate.
- Watchdog pattern for auto-shutdown is simple and effective for cost control.

## 2026-01-28 00:30 - [DevEx] Production Management CLI (prod_manage.sh + doh_customer)

Created a CLI toolchain for managing DOH production: a wrapper script to run Django management commands via ECS exec, and a comprehensive `doh_customer` management command for customer operations. This emerged from the need to debug and operate on production during the remote builder implementation.

**The problem:**

During debugging of the remote builder, I needed to frequently check deployment status, view logs, retry failed deployments, and inspect database state. Running ad-hoc Python in the Django shell via ECS exec was tedious and error-prone.

**Solution: Two-layer CLI:**

1. **`infra_devopshero/prod_manage.sh`** — Wrapper script that handles AWS credentials, finds the running ECS task, and executes any Django management command via `aws ecs execute-command`. Usage: `./prod_manage.sh <command> [args...]`

2. **`doh_customer` management command** — Django command with subcommands for customer operations. Provides a structured interface instead of ad-hoc shell queries.

**prod_manage.sh implementation:**

- Loads AWS credentials from `../.env` (DOH_AWS_ACCESS_KEY, DOH_AWS_SECRET_KEY)
- Finds running task via `aws ecs list-tasks`
- Properly escapes arguments for nested shell execution
- Runs command via `aws ecs execute-command --interactive`

**doh_customer operations:**

- **`list`** — Show all organizations, AWS accounts, and environments with their status
- **`create-env`** — Create a new environment record (with optional `--provision` flag)
- **`provision-env`** — Trigger environment provisioning by setting status to PENDING
- **`list-apps`** — Show all apps with their workspace, repository, and build strategy
- **`list-deployments`** — Show recent deployments with status (color-coded)
- **`deployment-logs`** — Show logs for a deployment (most recent by default, or filter by `--app`)
- **`retry-deployment`** — Reset a failed deployment to PENDING for retry

**Refinements to deployment-logs:**

The initial implementation required `--app` flag. After using it during debugging, made several improvements:

- **Made `--app` optional** — If omitted, shows the most recent deployment globally. This is the common case when debugging: "what's the latest deployment doing?"
- **Added `updated_at` timestamp** — Critical for monitoring in-progress deployments. Shows when the deployment record was last touched.
- **Reverse chronological order** — Logs now show newest first. When debugging, you usually want to see the latest activity, not scroll through old entries.
- **Added timestamps to log lines** — Each log entry shows `[HH:MM:SS]` so you can see the timeline.
- **Color-coded status** — Green for running, red for failed, yellow for in-progress states.

**Skill documentation:**

Created `.claude/skills/prod-manage/SKILL.md` with usage examples for all operations. The skill teaches agents how to use the CLI for production operations.

**Usage example (real debugging session):**

```bash
# Quick status check - what's the latest deployment doing?
./prod_manage.sh doh_customer deployment-logs --limit 5

# Re-provision environment after code change
./prod_manage.sh doh_customer provision-env --slug default --aws-account "DevOps Hero AWS Account"

# Retry a failed deployment
./prod_manage.sh doh_customer retry-deployment --app simple-dashboard
```

**Key learnings:**

- Management commands with subcommands scale better than separate commands. One entry point, discoverable operations via `--help`.
- When building debugging tools, optimize for the common case. Showing the most recent deployment globally (no `--app` required) eliminates a lookup step in 90% of uses.
- Reverse chronological order and timestamps are essential for debugging time-sensitive operations like deployments.
- ECS exec is powerful but requires careful argument escaping for nested shell execution.

## 2026-01-27 15:45 - [UI] Landing Page Conversion from React to Django Templates

Converted the React/TypeScript landing page (built with Bolt.new in `tmp/project/`) to Django templates. The original React version had 8 components using Tailwind 3, Lucide React icons, and React hooks for interactivity. The new Django version replicates the design without any React dependencies.

**Template structure decision — partials over monolithic:**

Created a `templates/devopshero_app/landing/` folder with 9 files:
- `landing_page.html` — main template that includes all partials, loads fonts, contains all JS
- `_header.html`, `_hero.html`, `_problem.html`, `_solution.html`, `_features.html`, `_personas.html`, `_cta.html`, `_footer.html`

Chose partials for maintainability even though the total is ~700 lines. Each section is self-contained and can be edited independently. The main template handles the JS for all interactive features.

**Tailwind 4 custom colors via @theme:**

The React version used Tailwind 3's `tailwind.config.js` to define custom `navy` and `cyber` color palettes. In Tailwind 4, custom colors go in CSS using `@theme`:

```css
@theme {
  --color-navy-900: #0a0f1c;
  --color-navy-950: #060911;
  --color-cyber-400: #22d3ee;
  --color-cyber-500: #06b6d4;
  /* etc */
}
```

This enables `bg-navy-950`, `text-cyber-400` etc. as utility classes. Also added custom utility classes for the landing page: `.gradient-text`, `.gradient-text-glow`, `.btn-primary`, `.btn-secondary`, `.section-padding`, `.container-custom`, `.bg-grid-pattern`, `.bg-radial-gradient`.

**Icon strategy — inline SVGs over library:**

The React version used `lucide-react`. For Django templates without a build step, chose inline SVGs copied from lucide.dev. This avoids JS dependencies and gives full CSS control via `currentColor`. The landing uses ~15 unique icons across all sections.

**Vanilla JS for interactivity:**

Replicated React hooks with vanilla JS in a single `<script>` block:
- Header scroll effect (adds blur/border background when scrolled past 20px)
- Mobile menu toggle with icon swap animation
- IntersectionObserver for scroll-triggered fade-in animations
- Animated counters in the Problem section (counts up when visible)
- Terminal animation loop in the Solution section (steps appear sequentially, then success message, then restarts)

**Known issue — button padding conflict:**

The `.btn-primary` class uses hardcoded `padding: 1rem 2rem`, which doesn't get overridden by utility classes like `py-2 px-6` on individual elements. The React version used `@apply` which allows overrides. Result: form buttons appear larger than the React original. Would need to either remove padding from `.btn-primary` or restructure to allow overrides.

**Key points:**
- Tailwind 4 uses `@theme` in CSS instead of `tailwind.config.js` for custom design tokens
- Inline SVGs are the cleanest approach for icons in server-rendered templates — no dependencies, full CSS control
- When converting CSS utility classes from React, avoid hardcoding values that should be overridable — use CSS custom properties or structure classes to allow utility overrides
- The conversion preserves all visual design and interactivity without any React/Node dependencies

## 2026-01-27 01:00 - [DevEx] Debug Production Skill

Created a new skill for debugging DOH production infrastructure issues. The skill provides context needed to investigate ECS tasks, CloudFormation stacks, and logs in the control plane.

**Design evolution toward minimalism:**

Started with a verbose version containing full AWS CLI command examples for every operation (list log streams, get log events, ECS exec, describe stacks, etc.). User correctly pointed out that the agent is smart enough to construct these commands — what it actually needs is the DOH-specific context it can't infer: resource names, log groups, stream prefixes.

Stripped down to just the essential context: ECS cluster name, service name, container names, log group, log stream prefixes, and CDK stack list.

**Dynamic extraction from source:**

Instead of hardcoding resource names in the skill (which could drift out of sync), created `extract_resources.py` that parses the CDK source files using regex to extract current values. The script reads `infra_devopshero/app.py`, `cluster_stack.py`, and `app_stack.py` to pull out resource names.

**Robust path resolution:**

Initially used fragile `.parent.parent.parent.parent` chain to find the infra directory. Replaced with a walk-up-the-tree approach that looks for `infra_devopshero/` directly at each parent level — works regardless of where the script lives in the repo.

**Key points:**
- Skills should provide context the agent can't infer, not instructions it already knows
- Dynamic extraction from source prevents skill/code drift
- Walk-up-tree pattern for finding directories is more robust than counting parents
- Tested the skill live — successfully retrieved ECS logs on first real use

## 2026-01-27 00:30 - [DevEx] Journaling Skill and Category System Overhaul

Created a `/journal` skill to standardize development journaling and overhauled the category system for both journal entries and beads tasks. The skill is triggered by `/journal` (just adds entry) or `/journal commit` (adds entry and commits all session changes).

**Category system redesign (from 7 to 9 categories):**

The original beads categories had unclear boundaries, particularly around infrastructure and deployment. After analysis:

- **CustomerInstall + Deployment → Deployment** — Merged because deploying apps can also create infrastructure (e.g., datastores). The distinction was artificial since both happen in customer AWS accounts.
- **AWSAccounts → Integrations** — Expanded to cover all external services: AWS accounts, GitHub App, WorkOS. The original name was too narrow.
- **Workspaces + Architecture → DomainModel** — Merged because "Workspaces" only named one entity while covering all domain objects. Architecture (refactors, design changes) naturally fits with domain model work.
- **Dashboard → UI** — Renamed for broader scope: shared frontend components, design system, base templates, not just the dashboard page.
- **ControlPlane (new)** — DOH's own infrastructure (EFS, Aurora, CloudFront, ALB). Critical to distinguish from customer infrastructure.
- **DevEx (new)** — Developer tooling, scripts, CLI, local development.
- **Bugfix (new)** — Bug fixes and debugging sessions.

**Final 9 categories:** Onboarding, AgentChat, Integrations, Deployment, DomainModel, UI, ControlPlane, DevEx, Bugfix.

**Journal entry format change:**

Added `[Category]` tag to entry titles for searchability: `## YYYY-MM-DD HH:MM - [Category] Title`. This enables grep filtering by category.

**Style change from concise to thorough:**

Initially the skill emphasized conciseness ("one or two sentences per point"). Changed to thoroughness ("include enough detail to understand context months later") because the journal serves as long-term documentation of decisions and their reasoning. Concise entries often lose the "why" that makes them valuable months later.

**Implementation details:**
- Skill location: `.claude/skills/journal/SKILL.md` (project skill, not personal)
- Updated AGENTS.md: removed Journal Writing section (now in skill), updated Task Categories
- Updated existing beads issues to use new category names via `bd update --title`


## 2026-01-27 - Workspace View UI Cleanup

Simplified the workspace detail page header by removing the "New Conversation" button. Reasoning: no clear use case for workspace-level conversations without a repository context yet — better to add it back when the need emerges (YAGNI).

Moved "New App" from a floating header button to a dashed-border card at the end of the apps grid. This integrates the action with the content it relates to and scales naturally whether there are 0 or many apps.


## 2026-01-27 - Chat Auto-scroll Toggle

Previously, the chat panel auto-scrolled to bottom on every SSE event, making it impossible for users to scroll up and read message history during streaming.

**Solution:** Added an auto-scroll toggle with a floating "scroll to bottom" button.

- **Initial state:** auto-scroll enabled
- **Disabled by:** `wheel` or `touchstart` events (user intent to scroll up)
- **Re-enabled by:** clicking the floating button (explicit action only)

**Implementation:** Added `autoScrollEnabled` state and gated all `scrollToBottom`/`scrollToBottomSmooth` calls through `maybeScroll*` wrappers. The floating button is positioned outside the scrollable container (relative to `#chat-panel-content`) so it stays fixed while content scrolls.

**Design decisions:**
- Used user-intent events (`wheel`, `touchstart`) rather than `scrollend` to disable auto-scroll immediately when user starts scrolling
- Button click uses instant scroll (not smooth) for immediate feedback
- No automatic re-enable based on scroll position — only explicit button click re-enables


## 2026-01-27 - Reuse Existing ACM Certificates and Fix Domain Selection UX

**Problem 1:** When deploying the shared ALB with HTTPS, we always created a new wildcard certificate even if one already existed. This caused duplicate certificates and potential conflicts.

**Solution:** Added `acm_utils.py` with `find_wildcard_certificate()` that searches ACM for existing certificates. Key insight: certificates can have the wildcard domain in Subject Alternative Names (SANs), not just the primary domain. Initial implementation only checked primary domain and missed certificates like `devopshero.ai` with `*.devopshero.ai` in SANs.

**Problem 2:** When creating environments, the agent would guess a domain (e.g., `devopshero.ai`) without calling `list_hosted_zones`, presenting a yes/no question instead of showing all available options.

**Solution:** Strengthened system prompt instructions in the "Environment Provisioning" section with explicit numbered steps, example output format, and CRITICAL rules. The agent must call `list_hosted_zones` first and present ALL domains as numbered options before creating an environment.

**Debugging approach:** Used CloudFormation stack events to diagnose failures. First failure was the ACM SAN bug. Second failure was a Route53 conflict — `*.devopshero.ai` record already existed (owned by DOH CDN stack). Used Django admin message log to confirm agent wasn't calling tools before making assumptions.


## 2026-01-27 - Fix Phantom Bubbles and Missing Tool Spinners on Page Refresh

Refreshing the page mid-conversation caused phantom empty message bubbles and lost tool spinner state.

**Root cause:** The SSE reconnect replay logic only tracked text streaming state (`accumulated_text`), not in-progress tool calls. After `text_flush` (which persists text to DB before tool execution), `accumulated_text` was empty but `is_streaming` remained true. On reconnect, this sent an empty `start` event creating a phantom bubble. Additionally, in-progress tools had no replay mechanism.

**Fix:** Track `pending_tools` in `AgentRunner` (tool_use_id → event data). On reconnect:
- If tools are pending → replay `tool_start` events (restores spinners)
- Else if text is streaming with content → replay text
- Otherwise → no replay (no phantom bubbles)

Also gate text replay on `accumulated_text` being non-empty, preventing empty `start` events after `text_flush`.


## 2026-01-27 - Include AWS Infrastructure in Agent System Context

Agent previously had to call `list_aws_accounts` and `list_environments` tools to discover available infrastructure before each deployment. This added latency and tool call overhead.

**Change:** Now inject all AWS accounts and environments directly into the system prompt. The agent sees the infrastructure immediately and can reference it without discovery calls.

**Implementation:** Added `_build_aws_infrastructure_section()` in `agent_service.py` that queries all accounts for the conversation's organization with prefetched environments. Updated system_prompt.md to reference this section instead of instructing the agent to call discovery tools.

**Format in system prompt:**
```
## AWS Infrastructure

**Production AWS** (id: abc123, aws: 123456789012, status: connected)
  - **default** (id: xyz789, slug: default, region: us-east-1, status: ready, domain: *.example.com)
```


## 2026-01-27 - EFS for Claude Session Persistence

Claude SDK stores conversation sessions locally in `~/.claude/`. When ECS tasks are replaced (deployments, restarts), sessions were lost — causing "No conversation found with session ID" errors when resuming.

**Solution:** Added EFS filesystem mounted at `/home/appuser/.claude` to persist sessions across container replacements.

**Implementation:**
- Created EFS filesystem in `storage_stack.py` with encryption enabled
- Added EFS Access Point with POSIX user UID/GID 1000 (matches `appuser`) — required for correct file ownership
- Mounted EFS in task definition with transit encryption and IAM authorization
- Added `elasticfilesystem:ClientMount` and `elasticfilesystem:ClientWrite` permissions to task role
- Set `CLAUDE_CONFIG_DIR=/home/appuser/.claude` environment variable

**Key learnings:**
- `CLAUDE_CONFIG_DIR` env var controls where Claude stores data (discovered via web search)
- Claude handles concurrent access from multiple instances (designed for desktop use with multiple terminals)

**Explicit UID in Dockerfile:** Updated `useradd` to explicitly set `--uid 1000 --gid 1000` so the EFS Access Point configuration isn't brittle. Added comment documenting the dependency.

**What is an EFS Access Point?**

An Access Point is a custom entry door into EFS with pre-configured settings. Without one, EFS mounts are root-owned and apps need root to write. With an Access Point:

- **Enforced user identity** — All file operations are performed as a specific UID/GID, regardless of the process's actual user
- **Enforced root directory** — The app sees a subdirectory as its root (chroot-like isolation)
- **Auto-create directory** — EFS creates the path with specified ownership if it doesn't exist

We configured `posix_user=PosixUser(uid="1000", gid="1000")` so when `appuser` (UID 1000) writes files, EFS performs the operation as UID 1000 and the files end up with correct ownership. No need for the container to run as root or use entrypoint scripts with `chown`.


## 2026-01-27 - Fix AssumeRole Permission for Customer Accounts

Agent tools failed with "AccessDenied" when trying to assume customer-installed IAM roles. The `doh-prod-task-role` lacked `sts:AssumeRole` permission.

**Root cause:** `DOH_AWS_ACCESS_KEY` / `DOH_AWS_SECRET_KEY` weren't configured in production Secrets Manager. When these are `None`, boto3 falls back to the ECS task role credentials, which didn't have AssumeRole permission.

**Fix:**
- Added `sts:AssumeRole` permission to task role for `arn:aws:iam::*:role/devopshero-*`
- Updated `iam_utils.py` to gracefully fall back to default credential chain when explicit credentials not provided

The wildcard account ID is intentional — DOH deploys to customer accounts. Security is enforced by customer role trust policies (require our account + ExternalId).


## 2026-01-27 - Use T-Shirt Sizes for Container Resources

Agent was presenting ECS CPU capacity as "256 CPU, 512 MB" which sounds like 256 processors. ECS uses CPU units where 1024 = 1 vCPU, so 256 units = 0.25 vCPU — confusing for users unfamiliar with AWS internals.

**Fix:** Updated system prompt to use t-shirt sizes (XS, Small, Medium, Large) with human-readable vCPU values. Agent now says "XS (0.25 vCPU, 512 MB)" instead of "256 CPU". Added mapping table so agent knows internal values while presenting user-friendly terms. Also added CPU/memory documentation to the `create_app` tool schema.


## 2026-01-27 - Fix AI Assistant in Production

Three issues preventing the AI assistant from working in production:

1. **Missing `CLAUDE_CODE_USE_BEDROCK=1`** — The agent client checks for this env var to enable Bedrock mode. The Bedrock credentials were in Secrets Manager, but the flag wasn't set. Added to `app_stack.py` environment.

2. **Missing `git` in Docker image** — Agent clones user repos via `subprocess.run(["git", ...])`, but `git` wasn't installed. Added to Dockerfile apt-get.

3. **Container running as root** — Claude SDK refuses `permission_mode="bypassPermissions"` when running as root for security. Added non-root `appuser` to Dockerfile and switched with `USER appuser`.


## 2026-01-26 - Fix Job Worker select_for_update Error

Job worker was failing with `FOR UPDATE cannot be applied to the nullable side of an outer join`. Root cause: `_claim_pending_teardown()` used `select_for_update()` with `select_related("app__datastore")`, and `datastore` is a nullable FK which creates a LEFT OUTER JOIN.

**Fix:** Removed `app__datastore` from `select_related()` in the claim function. The teardown executor re-fetches the deployment with all relations anyway, so the eager load in the claim was redundant.

Also expanded `infra_devopshero/AGENTS.md` with production debugging instructions (CloudWatch log commands for listing streams, fetching logs, filtering errors).


## 2026-01-26 - Enable Job Worker in Production

Added `DOH_RUN_JOB_WORKER=1` to the app container's environment in `app_stack.py`. The job worker was not running in production because the env var wasn't set — jobs (deployments, environment provisioning, teardowns) were stuck in PENDING.

The worker runs as a daemon thread within the web process via Django's `AppConfig.ready()`. This is fine for current volume; can migrate to a separate ECS service later if resource contention becomes an issue.


## 2026-01-26 - Move deployable_repos to Separate Repository

Moved `deployable_repos/` to a separate Git repository at `vmendi/deployable-repos` (private). This reduces the main repo size and separates example apps from the core platform.

**Changes:**
- Created new repo at `../deployable-repos` (sibling directory)
- Updated `seed_local_repos` command with `--path` argument (defaults to `../deployable-repos`)
- Added `--exclude` argument to skip specific repos (e.g., `--exclude=db_portal`)
- Updated `example_apps.py` and `test_repo_analysis.py` to use new path
- Removed `deployable_repos/` from `.dockerignore` (no longer needed)
- Added `.gitignore` to deployable-repos to exclude `node_modules/`, `_build/`, `deps/`, etc.
- Removed `internal_admin_dashboard/` (full NetBox clone, 60MB) from deployable-repos

**Repo size:** deployable-repos went from 296MB to ~15MB after cleanup.


## 2026-01-26 - Add deploy_app.sh for Fast App-Only Deployments

Created `infra_devopshero/deploy_app.sh` for deploying code changes without running CDK stack checks. The full `deploy.sh` takes 10+ minutes even with no infrastructure changes because CDK synthesizes and compares all 6 stacks.

The new script only builds the Docker image, pushes to ECR, and triggers ECS force-new-deployment (~2-3 minutes). Added `--sync-secrets` flag for optionally syncing secrets from `.env` to AWS Secrets Manager before deploying.


## 2026-01-26 - Production Latency Investigation and DB Connection Pooling

Investigated why production latency (~250ms) was much higher than localhost (~30ms).

**Diagnosis approach:**
- Created `curl-format.txt` to measure timing breakdown (DNS, connect, TLS, transfer)
- Compared CloudFront path vs direct ALB to isolate components
- CloudFront warm: ~140ms, Direct ALB: ~330ms (CloudFront faster due to edge TLS termination)
- Checked CloudWatch RDS metrics and ECS logs

**Root cause of high backend time:** Django's `CONN_MAX_AGE` was unset (default 0), meaning every request opened a new TCP connection to Aurora, performed TLS handshake, and authenticated — adding ~30-50ms per request.

**Initial fix:** Added `conn_max_age=600` (10 minutes). This caused DB connections to spike from 0 to 57.

**Why 57 connections (more than 40-thread pool)?**
- Uvicorn uses AnyIO's threadpool (default 40 threads) for sync views
- Each thread that handles a DB request keeps its connection alive for CONN_MAX_AGE
- Async views (`chat_stream`) and async agent tools (19 files) create connections outside the threadpool
- Multiple deployments during testing caused connection stacking until old ones expired

**Final fix:** Reduced `CONN_MAX_AGE` to 60 seconds. Balances latency benefit vs connection buildup.

**Latency breakdown for US West user → us-east-1 infrastructure:**
- You → CloudFront edge: ~20ms
- CloudFront → us-east-1 (cross-country round trip): ~70ms
- TLS handshakes: ~25ms
- App processing: ~25ms
- **Total: ~140ms** (unavoidable without moving region)

**Key learnings:**
- `CONN_MAX_AGE` is essential for production Django with network databases
- Uvicorn's AnyIO threadpool defaults to 40 threads — configurable via `anyio.to_thread.current_default_thread_limiter().total_tokens`
- Async Django ORM creates connections outside the threadpool, so total connections = threadpool + async coroutines
- Cross-region latency (~70ms round trip US West ↔ US East) dominates for geographically distant users
- Health check endpoint (`/health/`) doesn't hit DB — no session cookie means SessionMiddleware skips DB
- Aurora Serverless v2 handles 2000+ connections easily; 57 connections caused ~60% ACU utilization but isn't a problem


## 2026-01-26 - Fix Lambda Missing DOH_API_SECRET_KEY (401 on Callback)

After fixing the Lambda name mismatch, the install callback still failed with 401. The Lambda was sending an empty `Authorization: Bearer ` header because `DOH_API_SECRET_KEY` wasn't being loaded from `.env` during CDK deployment.

**Root cause:** `deploy.sh` only loaded AWS credentials (`DOH_AWS_*`) via grep, not the other env vars needed by the Lambda stack. The Lambda stack reads `DOH_API_SECRET_KEY` from the shell environment at deploy time and bakes it into the Lambda's environment variables.

**Attempted fix:** Tried `set -a; source .env; set +a` to load all env vars, but this broke due to `GITHUB_APP_PRIVATE_KEY` containing special characters (`\n` escapes in the PEM key).

**Actual fix:** Added `DOH_API_SECRET_KEY` and `DOH_API_ENDPOINT` to the selective grep loading in both `deploy.sh` and `deploy_stack.sh`.

**Future improvement:** Consider fetching `DOH_API_SECRET_KEY` from Secrets Manager at Lambda runtime (like ECS does) instead of baking it in at deploy time. This would eliminate the need to load it in deploy scripts.


## 2026-01-26 - Fix Lambda Name Mismatch in CF Install Template

First AWS account integration failed with "Function not found" error. The CloudFormation template referenced `devopshero-install-callback` but CDK deploys the Lambda as `doh-prod-install-callback`.

**Fix:** Updated `cf_install_template.json` ServiceToken to use the correct function name `doh-prod-install-callback`.

Also added `deploy_stack.sh` helper script to deploy individual CDK stacks without running the full `deploy.sh`.


## 2026-01-26 - S3 BucketDeployment and CORS for Customer CF Templates

CloudFormation Quick Create Stack failed when customers tried to connect their AWS accounts. Two issues discovered:

**Issue 1: Empty bucket (Access Denied)**
The public S3 bucket had correct permissions but was empty. CDK creates infrastructure but doesn't automatically upload files. The `cf_install_template.json` was accidentally deleted on Jan 20th when cleaning up `_old_cf/`.

**Issue 2: Missing CORS (TypeError: Load failed)**
After uploading the template, Quick Create Stack still failed with "TypeError: Load failed". The CloudFormation console runs in the browser and fetches templates via JavaScript — requires CORS headers.

**Fix:**
1. Restored `cf_install_template.json` from git history (`git show 'ef6891a0b^:...'`)
2. Added `BucketDeployment` construct to automatically upload templates on deploy
3. Added CORS rule to public bucket allowing GET from any origin

**Two template files to understand:**
- `infra_devopshero/cf_install_template.json` — Customer runs this in their account. Creates IAM role + calls install callback Lambda.
- `infra_devopshero/_old/cf_install_callback_lambda.json` — DOH runs this to create the callback Lambda (now managed via `lambda_stack.py`).


## 2026-01-26 - Automated Superuser Setup via Init Container

Added `ensure_superuser` management command to automatically promote a configured user to Django admin on every deployment. This ensures admin access is reproducible without manual intervention.

**The problem:** Django migrations don't create a superuser. Previously required SSH/ECS Exec with Session Manager Plugin to run `createsuperuser` or promote users manually — not reproducible and easy to forget on fresh deployments.

**Solution:** Custom management command + init container:
1. **`ensure_superuser` command** — Reads `DJANGO_SUPERUSER_EMAIL` from environment, promotes that user to `is_staff=True` and `is_superuser=True`. Idempotent: skips if already superuser, logs message if user doesn't exist yet (will be promoted on next deployment after they log in).
2. **Init container updated** — Now runs `migrate --noinput && ensure_superuser` in sequence.
3. **Secret configuration** — Added `DJANGO_SUPERUSER_EMAIL` field to `devopshero/prod/django` secret in AWS Secrets Manager.

**To configure for new deployments:**
1. Add `DJANGO_SUPERUSER_EMAIL=your@email.com` to `.env`
2. Run `cd infra_devopshero && uv run python sync_secrets.py` to push to AWS Secrets Manager
3. The email should match the WorkOS login email of the intended admin
4. User must log in at least once (to create their account) before or after deployment

**Gotcha:** The `sync_secrets.py` script overwrites secrets entirely based on `SECRET_DEFINITIONS`. If you manually add fields to a secret in AWS console, they'll be wiped on next sync. Always add new secret fields to both `.env` and `SECRET_DEFINITIONS` in `sync_secrets.py`.

**ECS Exec alternative (requires Session Manager Plugin):**
```bash
aws ecs execute-command --cluster doh-prod-cluster --task <TASK_ARN> --container devopshero --interactive \
  --command "uv run python manage.py shell -c \"from devopshero_app.models import User; u = User.objects.get(email='user@example.com'); u.is_staff=True; u.is_superuser=True; u.save()\""
```


## 2026-01-26 - CloudFront Static Files 502 Fix

Static assets (`/static/*`) returned 502 errors after deploying the migration init container. Root cause: the `/static/*` CloudFront behavior was missing an `origin_request_policy`, so CloudFront didn't forward the Host header to the ALB.

**Why it broke now:** A previous change switched CloudFront origin protocol from `HTTP_ONLY` (port 80) to `HTTPS_ONLY` (port 443). With HTTP, the missing policy wasn't a problem. With HTTPS, the Host header became essential for proper TLS negotiation and routing.

**Diagnosis steps:**
- ALB served CSS correctly when accessed directly (`curl -sk https://alb-hostname/static/...`)
- CloudFront returned 502 for `/static/*` but 200 for `/`
- Difference: default behavior had `origin_request_policy=ALL_VIEWER`, static behavior had none
- Cache invalidation didn't help — CloudFront was actively failing, not serving cached errors

**Fix:** Added `origin_request_policy=ALL_VIEWER` to the `/static/*` behavior. Applied via AWS CLI because CDK deploy failed with a separate web ACL error (CloudFront pricing plan subscription requires web ACL).

**CDK code updated** in `cdn_stack.py`:
- Added `origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER` for the static behavior


## 2026-01-26 - ECS Migration Init Container

Added a migration init container to the ECS task definition so Django migrations run automatically before the app starts on every deployment.

**The problem:** Database was connected but migrations hadn't run. Previously there was no automated way to run `manage.py migrate` in production.

**Solution:** Init container pattern using ECS container dependencies:
1. **Migration container** — Runs `manage.py migrate --noinput`, marked `essential=False` so task continues after it exits
2. **App container** — Has `ContainerDependency` with `condition=SUCCESS`, so it waits for migration to complete before starting

**Initial failure:** Migration container crashed because Django's migrate command runs system checks, which loads URL configs, which imports `auth.py`, which initializes the WorkOS client at module level. The migration container only had database secrets, not WorkOS secrets.

**First fix attempt:** Added `--skip-checks` to bypass Django system checks. This worked but was suboptimal — checks provide valuable validation before running migrations.

**Proper fix:** User refactored `auth.py` to lazy-initialize the WorkOS client (not at module import time). Then removed `--skip-checks` and gave the migration container all the same secrets as the app container. This ensures migration runs in an identical environment to the app.

**Code changes in `app_stack.py`:**
- Extracted secret references to variables (e.g., `django_secret`, `workos_secret`) to avoid duplicate CDK construct IDs
- Created `app_secrets` dict combining database and app secrets
- Both containers now use `secrets=app_secrets`
- Migration command: `["uv", "run", "python", "manage.py", "migrate", "--noinput"]`

**Deploy flow now:**
1. ECS starts new task
2. Migration container runs, applies any pending migrations
3. Migration exits successfully (code 0)
4. App container starts (was blocked on migration SUCCESS)
5. Health check passes, traffic routes to new task

Migration logs go to CloudWatch under the "migrate" stream prefix, separate from "devopshero" app logs.


## 2026-01-26 - CloudFront to ALB HTTPS Communication

Fixed OAuth redirect URI using `http://` instead of `https://` in production. Root cause: CloudFront terminated HTTPS and forwarded to ALB over HTTP, so Django's `request.is_secure()` returned False.

**Infrastructure changes:**
- ALB now has HTTPS listener (port 443) with ACM certificate (same cert used by CloudFront)
- CloudFront origin protocol changed from `HTTP_ONLY` to `HTTPS_ONLY`
- Removed fixed `security_group_name` from ALB security group to allow CDK replacements

**Django changes:**
- Added `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` to trust ALB's forwarded protocol header
- Removed ngrok hack from auth.py — `request.is_secure()` now works correctly in all environments

The standard pattern: when ALB receives HTTPS, it sets `X-Forwarded-Proto: https`, and Django trusts that header.


## 2026-01-25 - CloudFront + ngrok Host Header Fix

Fixed devopshero.ai domain not resolving. Two issues discovered:

1. **Missing Route 53 A record** — CloudFront doesn't auto-create DNS records when you add an alternate domain name. Created alias A record pointing `devopshero.ai` → `d2yopfffsrimnp.cloudfront.net` (CloudFront hosted zone ID `Z2FDTNDATAQYW2` is fixed for all distributions).

2. **Host header mismatch** — ngrok returns HTTP 421 (Misdirected Request) if Host header doesn't match the tunnel name. CloudFront was forwarding `Host: devopshero.ai` from viewer requests, but ngrok expected `Host: devopshero.ngrok.io`. Fix required two policy changes:
   - Cache policy: `UseOriginCacheControlHeaders` → `Managed-CachingDisabled` (the original policy whitelisted Host header)
   - Origin request policy: Added `Managed-AllViewerExceptHostHeader` to forward all viewer headers except Host

When Host header isn't forwarded, CloudFront uses the origin domain name as the Host header automatically.


## 2026-01-25 - Add list_apps Tool to Prevent Duplicate Apps

Agent was creating duplicate apps with numeric suffixes (e.g., `simple-dashboard-2`) when users asked to "deploy again" after teardown. Root cause: agent had no way to discover existing apps in the workspace, so it always called `create_app`.

Added `list_apps` MCP tool that returns apps in the current workspace with their ID, name, slug, branch, repository, and latest deployment status. Updated system prompt to instruct agent to check for existing apps before creating new ones, and to use `deploy_app` with the existing app's ID for re-deployments.


## 2026-01-25 - ECS Deployment Stabilization Optimization

Reduced ECS service stabilization time from ~2 minutes to ~1.5 minutes by tuning health check and polling parameters. These settings prioritize fast dev iteration over zero-downtime guarantees.

**CDK changes (deploy_app.py):**
- ALB health check interval: 15s → 5s, timeout: 5s → 2s (healthy window: ~30s → ~10s)
- Target group deregistration delay: 10s → 5s
- ECS service min_healthy_percent: 100 → 0 (allows old task to stop before new fully healthy)

**Polling code changes (ecs_utils.py):**
- STABLE_CHECKS_REQUIRED: 3 → 2
- Poll interval: 10s → 5s
- Added rolloutState=COMPLETED check for more reliable stability detection (catches edge cases where running==desired during mid-rollout)

Production settings (higher min_healthy_percent, longer deregistration_delay) are marked with TODO comments.


## 2026-01-25 - Ask Codex Skill for External Perspective

Added a skill to query GPT-5.2 (via Codex CLI) for external perspective on current problems. Useful for architecture validation and getting a second opinion without conversation context bias.

Key fixes for non-interactive shell execution:
- **`-o <file>` flag** — Writes final answer to file, bypassing TTY buffering issues
- **`< /dev/null`** — Closes stdin to prevent command from waiting on input
- **`--full-auto`** — Ensures no approval prompts interrupt execution

Initial implementation hung indefinitely in the Shell tool. Root cause was `codex exec` waiting on stdin which never closed in non-interactive environments.


## 2026-01-25 - Relative Path Display in Tool Calls

Tool call UI now shows relative paths instead of absolute paths. Before: `/Users/.../tmp/conv-abc123/requirements.txt`. After: `requirements.txt`.

- Added `_relativize_sandbox_path()` using `pathlib` with `settings.CLAUDE_SANDBOX_DIR` as the single source of truth
- Added `sanitize_paths_for_display()` that recursively walks dicts/lists/strings
- Applied to tool header (via `_format_param_value`), parameters panel, and results panel (via `json_pretty` filter)
- Paths outside the sandbox remain unchanged as a security signal

Consulted Codex (GPT-5.2) for architecture validation. Key insight: apply transformation recursively to all nested values, not just the main parameter, and use pathlib instead of string prefix checks for robustness.


## 2026-01-24 - Chat Sidebar UX Improvements

Two fixes for the conversation sidebar:

- **Disabled clicks on selected item** — Added `pointer-events-none` to the currently active conversation to prevent redundant navigation. The JavaScript `updateActiveConversation()` also toggles this class when navigating via HTMX.

- **Fixed sidebar flash during transitions** — The View Transitions API was causing the sidebar background to briefly flash when switching conversations. Gave the sidebar its own `view-transition-name: chat-sidebar` and disabled its transition animation via CSS (`display: none` on old snapshot, `animation: none` on new). The chat panel content still transitions smoothly.


## 2026-01-24 - Dashboard Shows All Apps and Datastores

Replaced the "coming soon" dashboard placeholder with an org-wide view of all apps and datastores. Each card shows the item details plus a link to its parent workspace for navigation.

This provides the cross-workspace overview that was discussed when removing the top-level Apps/Datastores sidebar entries — users can see everything from Dashboard, then drill into specific workspaces.


## 2026-01-24 - Remove Apps/Datastores Sidebar Entries

Removed the top-level "Apps" and "Datastores" menu entries from the sidebar. These were stub pages ("coming soon") that added clutter without functionality.

Apps and datastores are already accessible within their workspace context — the workspace detail page shows both, and the "New App" flow starts from there. The workspace-first navigation model makes separate top-level entries redundant.

If cross-workspace views are needed later, they'll go on the Dashboard with a workspace filter dropdown.


## 2026-01-24 - Mid-Stream Reconnect Text Replay

Added accumulated text replay when clients reconnect mid-stream. Previously, if a user navigated away during streaming and came back, they'd see streaming resume but the beginning of the message was lost (those events were already consumed by the old SSE connection).

### The Problem

Events flow: `agent_service` → `runner.event_queue` → `chat_stream` SSE → client

When client disconnects mid-stream:
1. Old SSE stops consuming from queue (on `CancelledError`)
2. Agent continues, events keep going into queue
3. Client reconnects, new SSE starts consuming
4. But the `start` event and early `text_delta` events were already consumed by old connection

The new client receives orphan `text_delta` events with no streaming container to render into.

### Solution: Runner Tracks and Replays State

Added two fields to `AgentRunner`:
- **`is_streaming: bool`** — True between `start` and `complete` events
- **`accumulated_text: str`** — Current text block being streamed

The runner tracks streaming state as events flow through:
- `start` → set `is_streaming=True`, reset `accumulated_text`
- `text_delta` → append chunk to `accumulated_text`
- `text_flush` → reset `accumulated_text` (text block finalized before tool call)
- `complete` → set `is_streaming=False`, reset `accumulated_text`

On reconnect, `mark_client_connected()` checks if mid-stream and replays:
```python
if runner.is_streaming:
    runner.event_queue.put_nowait(AgentStreamEvent(type="start", data={}))
    if runner.accumulated_text:
        runner.event_queue.put_nowait(AgentStreamEvent(type="text_delta", data={"text": runner.accumulated_text}))
```

The client sees: `start` (creates container) → accumulated text (appears instantly) → live `text_delta` events continue.

### Why State Lives in Runner, Not agent_service

`agent_service.StreamingContext` already tracks `accumulated_content` and `has_started_streaming`, but it's the wrong place:

- **`StreamingContext`** is local to each `stream_response()` call. It's created fresh when the agent starts and lives inside the async generator. Purpose: track state for DB persistence at the end.

- **`AgentRunner`** persists across client reconnects. Same runner serves multiple SSE connections. Purpose: track state for client replay.

The runner can't access `StreamingContext` — it only sees events yielded out. Exposing accumulated state in events would be more invasive (each `text_delta` carrying full accumulated text). The duplication is pragmatic: simple string concatenation in two places for different purposes.

### put_nowait vs put

`mark_client_connected()` is a sync function, so we can't `await queue.put()`. Instead, `put_nowait()` adds to the queue synchronously. It would raise `QueueFull` on a bounded queue, but ours is unbounded (safe for finite agent responses), so it always succeeds.

### Clean Separation of Concerns

The SSE endpoint (`chat_stream`) is now a "dumb pipe":
1. `ensure_agent_running()` — get/spawn runner
2. `mark_client_connected()` — runner handles replay if needed
3. Pull from queue → forward to client

All streaming state and reconnect logic lives in the runner. The SSE endpoint knows nothing about `is_streaming` or `accumulated_text`.


## 2026-01-24 - Agent Task Decoupling Architecture

Major refactor to decouple agent processing from SSE request lifecycle. Previously, reloading the page mid-response would kill the agent (Uvicorn cancels async tasks on client disconnect), losing the in-progress response. On reconnect, the agent would restart from scratch.

### Problem

The agent ran inline with the SSE request:
```
SSE request → runs agent → yields events → client disconnect kills agent
```

When a client reloaded mid-stream:
1. Uvicorn raised `CancelledError`, killing the agent
2. Response wasn't persisted (agent hadn't finished)
3. On reconnect, `_needs_response()` was still True
4. Agent restarted, duplicating work

### Solution: Background Agent Tasks

New architecture separates concerns:
```
User sends message → spawns background agent task (if not running)
SSE request → subscribes to event queue from that task
Client disconnect → SSE dies, agent continues
```

Created `devopshero_app/services/agent/agent_runner.py` with:
- **`AgentRunner`** dataclass: holds task, event queue, client_connected flag
- **`_runners`** dict: in-memory registry keyed by conversation_id
- **`ensure_agent_running()`**: spawns runner if needed, uses double-checked locking
- **`_run_agent_loop()`**: polls DB for messages, runs agent, pushes events to queue

### Key Design Decisions

- **Agent owns the message polling loop** — The runner's internal loop checks for pending messages, not the SSE endpoint. SSE is a pure consumer.
- **Unbounded queue** — Safe because agent responses are finite. Avoids producer blocking if client disconnects mid-stream.
- **Runner exits when**: client disconnected AND no pending user message. Stays alive while client connected (waiting for input) OR work to do.
- **Double-checked locking** — Prevents race conditions when multiple requests try to spawn a runner for the same conversation.
- **No DB flag for "processing"** — Single-instance deployment means in-memory `_runners` dict is source of truth.

### Reconnect Behavior

When client disconnects and reconnects:
1. `ensure_agent_running()` returns existing runner (logged as "Reusing existing runner")
2. New SSE attaches to the same queue
3. Drains any buffered events from in-progress response
4. Continues receiving live events

### SSE Endpoint Simplification

`chat_stream` now:
1. Gets runner via `ensure_agent_running()`
2. Marks client connected
3. Consumes from queue until sentinel (None)
4. On `CancelledError`: marks client disconnected (doesn't kill runner)

Also simplified: single DB query using `request.auser()` + `organization_id` FK instead of two queries.

### Files

- **New**: `devopshero_app/services/agent/agent_runner.py`
- **Modified**: `devopshero_app/views/chat.py` — SSE becomes queue consumer


## 2026-01-24 - SSE Client Disconnection Logging

Added `CancelledError` handling to the SSE event generator in `chat_stream`. When a client disconnects (e.g., page reload), Uvicorn cancels the async task, which raises `CancelledError`. This stops the agent stream cleanly rather than leaving it orphaned. The handler logs the disconnection for observability.


## 2026-01-24 - Fix run_dev.py Port Binding Issues on macOS

Fixed "Address already in use" errors when running `uv run run_dev.py`:

- **Root cause**: `uvicorn.run()` with `reload=True` has socket binding issues on macOS. The Python API creates the socket before forking, causing race conditions.
- **Solution**: Use `os.execvp()` to run uvicorn CLI directly (same approach as Procfile.tailwind). The CLI handles socket binding correctly.
- **Added auto-cleanup**: Script now kills any existing process on port 8000 before starting, preventing stale process issues.


## 2026-01-24 - Chat Sidebar Shows Workspace and Repository

Updated conversation list sidebar to display workspace and repository context:
- Time ("X minutes ago") now on first line below title
- "Workspace: Name  Repository: Name" on second line when set
- Labels use lighter gray, values use brighter text (matching header style)
- Added `context_repository` to `select_related()` for efficient loading


## 2026-01-24 - AI-Generated Conversation Titles

Implemented automatic conversation title generation after the first agent response.

### LLM Client Service

Created `devopshero_app/services/llm/` for lightweight LLM calls (separate from Claude Agent SDK):
- `llm_client.py` — Client factory supporting both Anthropic API and Bedrock, mirroring agent_client.py config
- `title_generator.py` — Generates titles using Haiku with user message, agent response, and context

Centralized model names in `LLM_MODELS` dict with simple aliases (`opus-4.5`, `sonnet-4.5`, `haiku-4.5`) that map to correct format for API vs Bedrock. Settings now use aliases: `CLAUDE_MODEL=sonnet-4.5`.

### Title Generation Flow

1. Agent finishes first response → `ctx.accumulated_content` has response text
2. `_maybe_generate_title()` calls Haiku with: user message, agent response, workspace name, repo name
3. Title is set on conversation and included in `sse-complete` event data
4. JS `handleComplete(data)` processes OOB elements to update header and sidebar titles

Title OOB swap piggy-backs on `sse-complete` rather than a separate event — matches how `select_workspace` (now removed) used to bundle title updates with tool results.

### Cleanup

Removed dead `select_workspace` code from `chat.py` (tool was deleted in earlier commit but OOB swap code remained).


## 2026-01-24 12:30 - Repository Picker Modal Search and Pagination

Added client-side search and pagination to the repository picker modal:
- Search box filters by repo name or full name
- Pagination shows 10 repositories per page with Previous/Next navigation
- Escape key closes the modal
- State resets when modal opens (clears search, returns to page 1)
- Reduced row padding from `py-3` to `py-2` for denser list


## 2026-01-24 12:15 - Repository Picker Modal Scrollbar Styling

Applied consistent scrollbar styling to the repository picker modal. Added `dark-scrollbar` class, `scroll-smooth`, and `scrollbar-gutter: stable` to match the chat interface scrollbar appearance.


## 2026-01-24 11:30 - Integrate Local Repos into Repository Model

Replaced dynamic `list_deployable_repos` filesystem scan with persisted Repository records.

**The problem:** Two parallel discovery mechanisms — `list_deployable_repos` scanned `deployable_repos/` at runtime returning `file://` URLs, while `list_repositories` queried Repository model from DB. Agent needed two tools for the same conceptual operation.

**The solution:** New management command `seed_local_repos --org=<slug>` scans `deployable_repos/` and creates Repository records with `provider="local"` and `file://` clone URLs. Agent now uses unified `list_repositories` for all repo discovery.

Changes:
- Created `seed_local_repos` management command
- Deleted `list_deployable_repos.py` and removed from mcp_tools
- Removed dead code: `_parse_file_url()` and deprecated `scan_repository(url, branch)` from scan_repository.py
- Renamed `scan_repository_path` to `scan_repository`

Repository model already supported `provider="local"` and `file://` URLs. `repo_service.clone_repository` already handles `file://` URLs by copying directory contents.


## 2026-01-24 01:45 - Git Cloning and Agent Sandboxing

Implemented repository cloning with GitHub App authentication and enabled Claude Agent SDK sandboxing.

### Repository Cloning

New `repo_service.py` handles cloning:
- GitHub repos: Uses installation token in URL (`https://x-access-token:{token}@github.com/...`)
- Local `file://` URLs: Copies to sandbox dir (was returning path directly, changed for consistency)
- Re-uses existing clones for same conversation (clone_id based on conversation ID)
- Clones persist for conversation lifetime (no per-message cleanup)
- TODO: Implement cleanup on conversation close or via periodic job (see devopshero-3ur)

Clone path moved to `settings.CLAUDE_SANDBOX_DIR` (was `CLONE_BASE_DIR` in repo_service).

### Agent Sandboxing

`stream_response()` now clones repo when `conversation.context_repository_id` is set, passes path to agent options. Agent's `cwd` is set to the cloned repo directory.

Enabled `SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True)` to restrict agent's filesystem access.

### TMPDIR Fix

Sandbox initially crashed with `EOPNOTSUPP: watch` errors on `/var/folders/...` paths. The Claude CLI was trying to access macOS system temp (where VS Code sockets exist).

Root cause: `TMPDIR=/var/folders/...` in environment, which is outside the sandbox's allowed paths.

Fix: Override `TMPDIR` to point to our sandbox directory:
```python
env = {
    **get_claude_env(),
    "TMPDIR": str(settings.CLAUDE_SANDBOX_DIR),
}
```

Note: Claude docs mention `CLAUDE_CODE_TMPDIR` for internal temp files, but it's narrowly scoped. Standard `TMPDIR` covers all temp operations.

### Files Changed

- `repo_service.py` — Clone/cleanup functions
- `agent_service.py` — Sandbox config, repo cloning in stream_response
- `deployment_executor.py` — Uses repo_service for cloning
- `app_config_builder.py` — Accepts explicit repo_path parameter
- `mcp_tools.py` — scan_repository uses repo_service
- `settings.py` — Added `CLAUDE_SANDBOX_DIR`


## 2026-01-23 21:30 - Dev Server Reload Exclusions

Added `tmp/` exclusion to uvicorn's file watcher so cloned repos don't trigger reloads.

**The bug:** uvicorn's `--reload-exclude` with relative paths like `tmp` doesn't work. Watchfiles reports absolute paths, but uvicorn's `FileFilter` compares `Path("tmp") in path.parents` directly — a relative Path is never `in` an absolute path's parents.

**The fix:** Use absolute paths:
- `Procfile.tailwind`: `--reload-exclude "$(pwd)/tmp"` (shell expansion)
- `run_dev.py`: `reload_excludes=[str(Path("tmp").resolve())]`

Also added `run_dev.py` for debugger configs and standalone runs. Can't use it from Procfile because `uvicorn.run()` with `reload=True` spawns subprocesses that conflict with honcho's process management.


## 2026-01-23 19:45 - Conversation Context UI

Replaced agent-driven workspace/repository selection with UI-driven context selection. Users now enter conversations from the workspace page with context pre-set.

### Why This Change

The original flow required users to start a conversation, then use agent tools (`select_workspace`, `create_workspace`, `list_workspaces`) to establish context. This was awkward:
- Users had to explain what they wanted before the agent knew where to work
- Agent had to ask clarifying questions about workspace/repo selection
- Context wasn't visible until the agent responded

The new flow: users click "New Conversation" or "New App" from a workspace page, and context is set before the conversation starts.

### Two Entry Points

- **"New Conversation"** — Creates workspace-scoped conversation for managing existing apps
- **"New App"** — Opens repo picker modal, then creates workspace+repo scoped conversation for deploying a new app

This solves the chicken-and-egg problem: apps require a repository, but we can't select from existing apps when creating a new one.

### Implementation

- **Workspace detail page** (`/workspaces/<workspace_slug>/`) — Shows apps, datastores, recent conversations, with action buttons
- **Repo picker modal** — Lists organization's connected repositories for "New App" flow
- **Chat URL params** — `chat_new` accepts `?workspace=<id>&repo=<id>` to set context
- **Chat header** — Displays "Workspace: X" and optionally "Repository: Y" 
- **System prompt injection** — `_build_system_prompt()` appends context section with workspace/repo names and IDs
- **create_app tool** — Now gets repository from `context_repository_id` if not passed explicitly

### Removed Agent Tools

Deleted workspace management tools since workspaces are now UI-only:
- `select_workspace` — Context set via UI
- `create_workspace` — Workspaces created via UI (default auto-created on org setup)
- `list_workspaces` — Not needed without select/create

Updated system prompt to reflect that workspace context comes from UI, not agent tools.

### Learnings

- When renaming URL parameters (e.g., `slug` → `workspace_slug`), must update: URL pattern, view function parameter, template `{% url %}` tags, and any f-strings using the old name
- Templates must be exported from `views/__init__.py` to be accessible via `views.function_name`
- The `<slug:param_name>` syntax has two parts: converter type and parameter name


## 2026-01-23 - GitHub App Integration

Implemented GitHub App OAuth flow to connect organizations to GitHub and sync repositories.

### Design Decisions

- **GitHub App over OAuth App** — GitHub Apps provide org-wide installation, fine-grained permissions, and installation access tokens (short-lived, no PATs to manage).
- **Auto-sync on connect** — Repositories are fetched immediately after OAuth callback, so users see their repos right away.
- **Hybrid sync model** — Initial sync on connect, plus `sync_repositories` agent tool and UI "Re-sync" button for manual refresh.
- **Credentials split** — App-level credentials (ID, private key, client secret) in `.env`; per-org `installation_id` in database (`GitProviderIntegration` model).

### Implementation

- **GitHub service** (`services/github/github_client.py`) — JWT generation for App auth, installation token exchange, repo listing, sync logic that adds/updates/removes `Repository` records.
- **OAuth flow** (`views/github.py`) — `/github/connect` redirects to GitHub App installation, `/github/setup` callback creates `GitProviderIntegration` and triggers sync.
- **Settings UI** — New "Git Integrations" tab showing connection status, repo count, and Re-sync button.
- **Agent tools** — `list_repositories` queries local DB, `sync_repositories` re-fetches from GitHub API.
- **Webhook endpoint** (`/api/github/webhook`) — Stubbed for future auto-deploy on push.

### Configuration

Created GitHub App "DevOps Hero App" under DevOpsHeroAI organization with:
- Permissions: Contents (read/write), Metadata (read), Pull Requests (read/write)
- Events: Push (for future auto-deploy)
- Callback URL: `https://devopshero.ngrok.io/github/callback`
- Setup URL: `https://devopshero.ngrok.io/github/setup`

### Fixes During Testing

- Added `LOGIN_URL = '/auth/login/'` — Django's default `/accounts/login/` doesn't exist in this project.


## 2026-01-23 04:55 - Domain Model Refactor: Workspace as Governance

Major refactor to make Repository a first-class entity and transform Workspace into a governance-only container.

### Key Changes

- **New models:** `GitProviderIntegration` (org-level GitHub/GitLab connection), `Repository` (connected git repos)
- **Workspace simplified:** Removed `primary_repo_url`, `aws_account`, `aws_region` — now purely governance/policy container
- **App sources from Repository:** Added `repository` FK and `repo_subpath` to App model
- **Environment owns region:** Moved `aws_region` from Workspace to Environment
- **Conversation context:** Renamed `workspace` to `context_workspace`, added `context_repository` for UI-driven context selection
- **Default workspace signal:** Auto-creates "Default" workspace when Organization is created

### Rationale

Workspace was overloaded — it coupled governance (apps, policies) with infrastructure (AWS account/region) and source (repo URL). Splitting these concerns enables:
- Multiple repos per workspace (or multiple workspaces sharing repos)
- Apps in different repos deployed to the same governance container
- Future GitHub/GitLab integration as a separate concern

### aws_region Bug Fix

After refactor, `create_environment` wasn't setting `aws_region`, causing `sts..amazonaws.com` errors (empty region). Fixed by:
- Adding `aws_region` parameter to `create_environment` tool (defaults to "us-east-1")
- Updated tool description to instruct agent: confirm region with user before provisioning, since it can't be changed later


## 2026-01-23 00:30 - Add --yes flag to npx cdk command

Added `--yes` flag to the `npx cdk deploy` command in `cdk_utils.py`. Without this flag, npx prompts for confirmation when it needs to install the CDK CLI package, which blocks non-interactive deployments.


## 2026-01-22 04:50 - Convert deploy.py to Django Management Command

Converted the standalone `deploy.py` script to a Django management command `doh_deploy`. The script required manual `.env` path resolution and hardcoded AWS account IDs — both problems solved by leveraging Django's infrastructure.

### Why Management Command

- **`.env` loading** — Django settings already loads `.env` at startup, no path gymnastics needed
- **Database access** — Can look up AWS account by name from the database instead of hardcoding `TARGET_ACCOUNT_ID` and `TARGET_EXTERNAL_ID`
- **Consistent UX** — `python manage.py doh_deploy` fits the Django workflow

### Usage

```bash
python manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox"
python manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox" --teardown
python manage.py doh_deploy --base --account "Humanity Rules Sandbox" --env prod
```

### Changes

- **Added** `devopshero_app/management/commands/doh_deploy.py`
- **Deleted** `devopshero_app/services/infra_customer/deploy.py`
- **Simplified** `iam_utils.py` — Removed `load_credentials_from_env()`, now only contains `get_assumed_role_session()`


## 2026-01-22 03:45 - Async Environment Provisioning

Made `create_environment` non-blocking. Previously it waited 5-10 minutes for CloudFormation to complete, causing HTTP timeouts and poor UX. Now it returns immediately with PENDING status, and a background worker handles provisioning.

### Architecture

- **Job Worker Pattern** — Renamed `deployment_worker` → `job_worker` to handle multiple job types (deployments and environment provisioning)
- **EnvironmentLog Model** — Added parallel to `DeploymentLog` for tracking provisioning progress
- **Polling via `get_environment_status`** — New MCP tool for agents to check provisioning progress

### Flow

1. Agent calls `create_environment` → Creates `Environment` with `PENDING` status, returns immediately
2. Job worker claims it → Sets `PROVISIONING`, spawns thread
3. `environment_executor` runs CDK → Provisions VPC, ECS cluster, shared ALB
4. Updates to `READY` or `ERROR`
5. Agent polls with `get_environment_status` until ready

### Renamed Files

- `deployment_worker.py` → `job_worker.py`
- `deployment_logging.py` → `job_logging.py`
- `run_deployment_worker.py` → `run_job_worker.py`
- `DOH_RUN_DEPLOYMENT_WORKER` → `DOH_RUN_JOB_WORKER`

### New Files

- `environment_executor.py` — Runs provisioning (parallel to `deployment_executor.py`)
- `get_environment_status.py` — MCP tool to poll progress
- `EnvironmentLog` model + migration

### System Prompt Updates

Updated agent instructions to explain the async flow: check environments with `list_environments`, create with `create_environment`, poll with `get_environment_status` until READY, then proceed with deployment.


## 2026-01-22 02:30 - Simplified ALB Architecture: Removed Dedicated ALB + domain_name

Removed the option for per-app dedicated ALBs and the `domain_name` field from the App model. All apps now use the shared ALB exclusively.

### What Changed

- **Removed `domain_name` from App model** — Apps no longer have custom domain configuration. The URL is derived automatically as `{app_slug}.{environment.shared_alb_hosted_zone}`.

- **Removed dedicated ALB mode** — Previously `deploy_app.py` supported two modes: shared ALB (fast, uses host-based routing) and dedicated ALB (slow, creates per-app infrastructure). Removed the dedicated mode entirely.

- **Simplified `deploy_app.py`** — Renamed `AppWithAlbStack` → `AppStack`, removed `_setup_dedicated_alb()` method, removed `use_shared_alb` and `hosted_zone_id` parameters, removed unused ACM/Route53 imports.

### Why Simplify Now

The dedicated ALB mode was future-proofing for "full isolation" scenarios, but:
1. It significantly complicated the codebase with conditional paths
2. Shared ALB with host-based routing handles our current use cases well
3. If we need per-app ALBs later, we can reimplement with better clarity on actual requirements

### Domain Resolution

With this change, app domains are fully determined by the environment:
- Environment has `shared_alb_hosted_zone` (e.g., `dev.example.com`)
- App gets domain `{app_slug}.{shared_alb_hosted_zone}` (e.g., `my-app.dev.example.com`)
- No per-app domain configuration needed

This also resolves the earlier architectural concern about `domain_name` not fitting multi-environment scenarios (prod vs staging would need different domains per app).

### Files Changed

- `models.py` — Removed `domain_name` field
- `appconfig.py` — Removed `domain_name` from `AppConfig` dataclass
- `app_config_builder.py` — Removed `domain_name` from config construction
- `deploy_app.py` — Major cleanup: removed dedicated ALB, simplified to shared-only
- `deployment_executor.py` — Removed `use_shared_alb` parameter
- `example_apps.py` — Removed `domain_name` from example configs
- `create_app.py` / `mcp_tools.py` — Removed `domain_name` from tool
- `admin.py` — Removed from search fields
- Migration `0019_remove_app_domain_name.py` created


## 2026-01-21 21:10 - App Slug Uniqueness: Global → Per Organization

Changed `App.slug` from globally unique to unique per organization to fix a multi-tenant information leakage issue.

### The Problem

When generating app slugs, the system appends incrementing numbers if the slug already exists (e.g., `my-app` → `my-app-1`). With global uniqueness, this leaks information across tenants: if Tenant B tries to create "analytics-dashboard" and gets "analytics-dashboard-1", they learn that some other tenant already has "analytics-dashboard".

### Decision: Unique Per Organization

Considered three scopes:

- **Per Workspace** — Strongest isolation, but requires workspace slug in AWS resource names (longer, repetitive)
- **Per Organization** — Good isolation (orgs are tenant boundaries), keeps resource names short
- **Global** (previous) — Simplest AWS naming, but cross-tenant leakage

Chose **per organization** because:
1. Organizations are the tenant boundary — no cross-tenant leakage
2. AWS resource names stay short: `doh/{env.slug}/{app.slug}` still works since environments are per-account, accounts are per-org
3. Same-org collisions are acceptable (users probably want to know if a colleague already created that app name)

### Implementation: Denormalized FK

The constraint `unique_together = [["organization", "slug"]]` requires a direct FK from App to Organization. Since App → Workspace → Organization, we added a denormalized `organization` FK to App:

```python
class App(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        help_text="Denormalized from workspace for unique constraint",
    )
    workspace = models.ForeignKey(...)
    slug = models.SlugField(max_length=255)  # No longer unique=True

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                name="unique_app_slug_per_org",
            )
        ]
```

The `create_app` tool sets `organization=workspace.organization` and generates slugs with `App.objects.filter(organization=org, slug=slug)`.

### Async Gotcha: select_related for Organization

In `mcp_tools._get_workspace()`, added `select_related("organization")` so the organization is eagerly loaded when fetching the workspace. Without this, accessing `workspace.organization` in async code would require `sync_to_async` since Django lazy-loads related objects synchronously. Pre-fetching avoids this friction.

### Why Not Application-Level Validation Only?

Could skip the denormalized FK and just check uniqueness in `create_app.py`. Rejected because:
- Race conditions without DB-level constraint
- Other code paths (admin, future APIs) would need duplicate validation
- DB constraint is authoritative


## 2026-01-21 - ECS Exec command reference

To shell into a running ECS container for debugging:

```bash
aws ecs execute-command --cluster <cluster-name> \
    --task <task-id> --container <app-name> \
    --interactive --command /bin/sh
```

Removed this from deployment output logs since it cluttered the UI. Keep here for reference.

## 2026-01-21 07:25 - Deployment logging source + streaming output

- Replaced deployment log phase with a source field, added structured template/params logging, and capture CDK/Docker stdout/stderr into DeploymentLog. Added stderr downgrade logic for CDK output and hardened log formatting to avoid formatting exceptions.

## 2026-01-21 - Reduce ECS target group deregistration delay

Set ALB target group `deregistration_delay` to 10 seconds (default is 300). ECS service removal was taking minutes in "Draining" state while waiting for ALB connection draining. Marked as TODO for production where longer graceful draining may be needed.

## 2026-01-20 14:42 - Chat auto-scroll behavior

- Updated chat auto-scroll to unlock on user-initiated scrolling while reattaching immediately when the scrollbar reaches the bottom during streaming.

## 2026-01-20 22:03 - Secrets Manager CLI tools

Added CLI commands to `secrets_utils.py` for managing secrets in customer AWS accounts:
- `--list` — List all secrets (with optional `--include-deleted` for those in retention)
- `--purge-deleted` — Permanently delete secrets scheduled for deletion (bypasses retention period)
- `--account` — Explicit account selection by name or ID (defaults to 266117665083)

Core functions (`list_secrets`, `purge_deleted_secrets`) take a boto3 session parameter for reuse. The CLI handles Django setup and role assumption via `iam_utils`.

## 2026-01-20 14:09 - Fix markdown underscore rendering

- Switched chat markdown rendering to marked.js for both stored and streaming paths so intraword underscores display correctly.
- Updated streaming to buffer by line and re-render on newline/flush using the same renderer for a single codepath.

## 2026-01-20 - App Secrets Support in Agent Flow

Fixed `AccessDeniedException` when apps tried to read secrets from AWS Secrets Manager. The `app_secrets` field was defined in `AppConfig` but never populated through the agent flow.

### Root Cause

The `create_app` agent tool and Django `App` model didn't have an `app_secrets` field. When `app_config_builder.build_app_config()` built the config, `app_secrets` defaulted to `None`. This caused the CDK to skip adding the IAM policy for `secretsmanager:GetSecretValue`.

### Fix

Added `app_secrets` support through the full chain:
- Django `App` model — new JSONField for storing secret configuration
- `create_app` tool — new parameter with normalizer for LLM input quirks
- MCP tool schema — exposed parameter to agent
- `app_config_builder` — passes `app.app_secrets` to `AppConfig`
- System prompt — guidance for agent on detecting and configuring secrets

### Detection Pattern

The agent looks for Secrets Manager access patterns during repository analysis (GetSecretValue calls, config providers, `devopshero/{app}/secrets` paths) and populates `app_secrets` accordingly.

## 2026-01-20 - Moved infra_customer Inside Django App

Moved `infra_customer/` from project root to `devopshero_app/services/infra_customer/`. This eliminates the `sys.path.insert()` hack that was documented in the previous "sys.path Manipulation: Why It's Needed" entry.

The infrastructure code is now a proper Python package importable as `from devopshero_app.services import infra_customer`. Internal imports within the package use relative imports (`from . import deploy_app`).

## 2026-01-20 - Improved Tool Display Titles for Grep/Glob

Enhanced agent chat UI to show more context in tool call titles. Grep and Glob now display both the search pattern and target path (e.g., "Grep — foo.*bar in utils.py"). Also added special handling for Task tool to derive display name from sub-agent type.

## 2026-01-20 - Route53 Domain Discovery Agent Tool

Added `list_hosted_zones` agent tool to discover available Route53 domains in customer AWS accounts. This enables the agent to configure custom domains during deployment conversations.

### Design Decision: Agent-First Approach

Originally considered adding domain discovery to the AWS account connection UI flow with model changes (adding `default_domain` to AWSAccount, `domain` to Workspace). Instead, chose to let the agent handle domain discovery during conversation:

- **No model changes** — domain stays on App where it already is
- **No UI changes** — agent discovers domains on-demand via tool
- **Flexible** — agent can ask contextual questions about which domain to use
- **Fits the product vision** — "AI co-pilot" handles the complexity

### Agent Guidance

Updated system prompt with domain configuration rules:
- Single domain found → auto-select as default (e.g., `myapp.example.com`)
- Multiple domains → ask user which to use
- No domains → deploy with ALB DNS only

Also added requirement for deployment confirmation before proceeding, showing domain and other config clearly.

### Private Zones: Deferred

Filtering to public zones only for now. Private zones require either:
- Internal ALB (not implemented yet)
- Wildcard certificates already validated (workaround)
- Private CA support

Created bead `devopshero-3yg` for wildcard certificate reuse, which would also unlock private zone support.

## 2026-01-20 - Globally Unique App Names

Discovered a naming mismatch: ECS services were created with `{env_slug}-{app_name}` but started with just `app_name`. Investigation revealed deeper issues with the resource naming strategy.

### The Problem

The `resource_prefix` pattern was `devopshero-{env_slug}-{workspace_slug}-{app_name}`, producing names like `devopshero-default-acme-corp-simple-dashboard` (45+ chars). This caused several problems:

- **ALB/TG limits**: AWS limits these to 32 characters, forcing truncation that could cause collisions
- **Inconsistent naming**: Some resources used full prefix, others used shorter variants
- **Workspace collision risk**: The domain model says "multiple workspaces can deploy to the same environment" — if two workspaces had apps with the same name, they'd collide on resources that didn't include workspace_slug

### The Decision: Globally Unique App Names

Adopted the same constraint as Heroku, Render, and Railway: **app slugs must be globally unique across all workspaces**. This simplifies everything:

- **New resource prefix**: `doh-{env_slug}-{app_slug}` (e.g., `doh-default-simple-dashboard` = 26 chars)
- **New ECR path**: `doh/{env_slug}/{app_slug}`
- **Removed workspace_slug**: No longer needed in any resource naming

The `env_slug` remains because the same app can deploy to multiple environments (dev/staging/prod), each needing separate AWS resources.

### Why "doh" Instead of "devopshero"

"DevOps Hero" abbreviates to "DOH" (already in AGENTS.md). Using `doh-` instead of `devopshero-` saves 7 characters per resource name, keeping us well under AWS limits.

### Implementation

1. **Model**: Changed `App.slug` from `unique_together = [["workspace", "slug"]]` to `unique=True`
2. **Validation**: `create_app` now checks global uniqueness, not workspace-scoped
3. **Infrastructure**: Removed `workspace_slug` parameter from `deploy()` and `teardown()`
4. **CLI**: Removed `--workspace` argument from `deploy.py`
5. **AWS Resources**: Changed app-specific resource naming from `devopshero-*` to `doh-*`:
   - ALB, target groups, ECS services, task roles
   
**Not changed**:
- Base infrastructure (VPC, cluster, task execution role, log groups) — looks more aesthetic with full "devopshero", but decision might change in the future.
- Secrets Manager paths (`devopshero/{app}/...`)
- Cross-account AssumeRole name (`devopshero-{external_id}`) — installed in customer accounts via CloudFormation

### Naming Convention Decision: app_name vs app_slug in AppConfig

The `AppConfig` dataclass (infra layer) uses `app_name` but receives `app.slug` from Django. Considered renaming to `app_slug` for consistency but decided against it:

- **Bounded context translation**: Django uses "slug" as the canonical identifier; infrastructure uses "name" when creating AWS resources. The `app_config_builder` translates between these contexts.
- **Natural infra terminology**: `container_name=app_config.app_name` reads better than `container_name=app_config.app_slug`
- **Semantic accuracy**: The slug IS the name used for resources — the comment "used in resource names" is correct

This is acceptable translation between bounded contexts, not a naming inconsistency.

## 2026-01-20 - CDK Tokens: Runtime vs Synthesis Values

Hit an issue where `cluster.cluster_name` returned `${Token[TOKEN.42]}` instead of the actual cluster name when calling AWS SDK at runtime. The ECS service start failed with "Cluster not found."

**The problem:** When importing resources with `Fn.import_value()`, CDK returns tokens — placeholders resolved by CloudFormation during deployment, not by Python at runtime. The `ecs.Cluster.from_cluster_attributes()` call receives a token, and accessing `.cluster_name` just gives you that token back.

**Key insight:** CDK tokens are fundamentally unresolvable at Python runtime. There's no `Token.resolve()` method — that's by design. But you don't need `Fn.import_value` for values you already know.

**The fix:** Use a literal string for `cluster_name` in `from_cluster_attributes()` instead of `Fn.import_value()`:

```python
# Before (returns token)
cluster = ecs.Cluster.from_cluster_attributes(
    scope, "ImportedCluster",
    cluster_name=Fn.import_value(f"{prefix}-cluster-name"),  # Token!
    vpc=vpc,
)

# After (returns actual string)
cluster = ecs.Cluster.from_cluster_attributes(
    scope, "ImportedCluster",
    cluster_name=f"{prefix}-cluster",  # Literal string
    vpc=vpc,
)
```

Now `cluster.cluster_name` returns `"devopshero-default-cluster"` instead of `"${Token[TOKEN.42]}"`.

**Why this works:** The cluster name is deterministic (we define it), unlike VPC/subnet IDs which are AWS-generated. VPC attributes still need `Fn.import_value`, but cluster name doesn't. No separate `cluster_name: str` field needed in `EnvironmentInfrastructure`.

## 2026-01-19 - CDK Stack Refactor: Expose Environment Infrastructure

Exposed `environment_infra` as instance attribute on `AuroraClusterStack` and `AppWithAlbStack`. Previously a local variable, now accessible via `stack.environment_infra` after construction. Needed for external callers to access cluster name, VPC, etc.

Also standardized ECS service naming to use `resource_prefix` consistently (`devopshero-{env}-{workspace}-{app}`) instead of the shorter `{env}-{app}` pattern. This aligns service names with other resources and avoids potential collisions across workspaces.

## 2026-01-19 - LLM Tool Input Sanitization: The String-vs-Object Trap

Deployment crashed at `deploy_app.py:340` when iterating over `environment_variables`. The Django admin showed `"{}"` stored in the JSONField. Root cause analysis revealed a subtle but important lesson about LLM tool calls.

### The Bug

The `App.environment_variables` field expects `[{"name": "FOO", "value": "bar"}, ...]`. The code had:

```python
environment_variables=environment_variables or [],
```

This handles `None` and empty containers (falsy values), but Claude sent `"environment_variables": "{}"` — a **string** containing braces, not an empty object. Non-empty strings are truthy, so `"{}" or []` returns `"{}"`, which gets saved to the database.

### Why Claude Did This

The tool schema just said `"environment_variables": list` with no format guidance. Claude interpreted "no env vars needed" as the string `"{}"` rather than an empty array `[]`. This is a common LLM pattern — they sometimes stringify values when uncertain about the expected structure.

### The Fix

Two-part solution:

1. **Better documentation** — Updated tool description to explicitly show the expected format with an example: `[{"name": "API_KEY", "value": "secret"}]`. Pass `[]` if none needed.

2. **Defensive normalization** — Added `_normalize_environment_variables()` that handles all malformed inputs:
   - `None` → `[]`
   - String `"{}"` → parsed, rejected as non-list → `[]`
   - Dict `{}` → `[]`
   - Validates each item has `name`/`value` keys

### Lesson

When accepting structured data from LLMs, don't trust type hints alone. LLMs can send strings that look like the right type but aren't. Always validate and normalize inputs, especially for nested structures like `list[dict]`. The `x or default` pattern only catches falsy values — it won't save you from a truthy string that happens to contain JSON-like text.

## 2026-01-18 - Deployment Bridge: Closing the Loop from Django to AWS

Long planning session to implement the "deployment bridge" — making the agent actually deploy apps to AWS. Started with a blank slate: the `infra_customer/` CDK code worked from CLI, the agent could create `App` records, but nothing connected them.

### The Core Decision: Bridge vs. Code Generation

The design doc had two interpretations:

- **Option A: Agent generates CDK code** — Maximum flexibility, agent writes Python, system validates via `cdk synth`, errors fed back for retry. Requires sandbox execution.
- **Option B: Agent populates config, fixed CDK deploys** — Keep existing CDK stacks, agent transforms `RepoAnalysisOutput` → `AppConfig`. Safer, faster to ship.

**Decision: Option B (Bridge).** The existing CDK stacks work. The agent's job is configuration, not code generation. We can always add code generation later if patterns emerge that don't fit the templates.

### Worker Design: Polling Thread vs Asyncio

Considered in-process asyncio (`create_task` fire-and-forget) vs polling thread.

**Why polling thread wins:**

- **Natural decoupling** — The `deploy_app` tool just creates a DB record. Worker is a separate concern. If web process restarts between tool call and deployment starting, nothing is lost.
- **Blocking CDK code stays blocking** — No need to wrap `deploy_app.deploy()` in `run_in_executor`. Just call it.
- **Recovery** — On startup, worker can find deployments stuck in `BUILDING` (from a crash) and mark them failed.
- **Testable in isolation** — Run worker standalone, point it at DB, test without web layer.
- **1-second latency is meaningless** — Deployments take minutes. Who cares about 1 second?

The asyncio approach would require `run_in_executor` for CPU-bound CDK operations and has no recovery if the process dies mid-deployment.

**Migration cost is minimal** — When we add Celery/Django-Q later, only `worker.py` changes. The `run_deployment()` function stays identical.

### sys.path Manipulation: Why It's Needed

The `infra_customer/` directory is a **sibling** of `devopshero_app/`, not a child:

```
devopshero/
├── devopshero_app/          ← Django app, in INSTALLED_APPS, on sys.path
│   └── services/deployment/ ← Needs to import from infra_customer
├── infra_customer/          ← NOT a Django app, NOT on sys.path
│   ├── deploy_app.py
│   └── appconfig.py
```

Django puts the project root on `sys.path`, making `devopshero_app` and its children importable. But `infra_customer` is a sibling — Python doesn't search sibling directories. Adding `__init__.py` to `infra_customer` doesn't help because the directory itself isn't discoverable.

The `sys.path.insert(0, infra_customer_path)` hack makes it work. Alternatives (documented in code):
1. Make `infra_customer` a proper package (`pyproject.toml` + `uv pip install -e`)
2. Move `infra_customer` inside `devopshero_app`

For now, the hack is contained in one place and works. Revisit if it causes problems.

### The Bridge Architecture

Three-layer design:

```
Django Models (App, Workspace, Environment, Datastore)
        ↓
app_config_builder.py → appconfig.AppConfig
        ↓
deploy_app.py / deploy_base.py → CDK Stacks → CloudFormation
```

- **`app_config_builder.py`** — Converts Django `App` to `appconfig.AppConfig`. Builds ECR repo name with full context: `devopshero/{env}/{workspace}/{app}`. This naming ensures isolation.
- **`deployment_executor.py`** — Orchestrates the full deployment: get AWS session via AssumeRole, provision environment if PENDING, build AppConfig, call CDK, create logs.
- **`deployment_worker.py`** — Polling loop with atomic claiming via `select_for_update(skip_locked=True)`.

### Log Callback for Real-Time Progress

Added `log_callback: Callable[[str, str, str], None]` to CDK functions. The executor passes a callback that creates `DeploymentLog` entries:

```python
def log_callback(phase, level, message):
    DeploymentLog.objects.create(deployment=deployment, phase=phase, level=level, message=message)
```

Important while developing — we need to see what's happening. The agent can poll `get_deployment_status` to report progress to users.

### Environment Infrastructure Import Centralization

Created `EnvironmentInfrastructure` dataclass and `import_environment_infrastructure()` helper in `deploy_base.py`.

**Why centralize but each stack still imports?** CDK constraint: `Fn.import_value()` creates constructs scoped to a Stack. You can't import in Stack A and pass the construct to Stack B — they're separate CloudFormation templates. The helper centralizes the *logic*; each stack still calls it.

Named it `environment_infra` (not `env_infra`) and used directly (`environment_infra.vpc`) without extracting to local variables. Explicit is better.

### Lazy Environment Provisioning — The Key Insight

The flow handles base infrastructure provisioning correctly:

1. AWS account connects → API callback creates Environment in `PENDING` state
2. User deploys app → Creates Deployment in `PENDING` state
3. Worker picks up deployment
4. `run_deployment()` checks if environment is `PENDING` → provisions base infra first
5. Only then proceeds to `deploy_app.deploy()`

**Why this works:** `Fn.import_value()` is a CloudFormation intrinsic function. During CDK synthesis, it doesn't validate exports exist — it just generates `Fn::ImportValue` JSON. Resolution happens at CloudFormation deploy time. So base stacks can be deployed first, creating exports, then app stacks deploy and resolve the imports.

### Conversation-Deployment: FK → M2M

Changed from `Deployment.conversation` (FK) to `Conversation.deployments` (M2M).

**Why M2M?**
- Deployment becomes a cleaner domain entity, not coupled to chat
- Semantically, conversation "owns" the relationship — "this conversation triggered these deployments"
- More flexible if deployment is referenced from multiple conversations later

**Why not ArrayField/JSONField?** Denormalized, no referential integrity. M2M creates a proper join table with FKs and indexes.

After creating deployment, `mcp_tools.py` links it: `await conversation.deployments.aadd(deployment)`

### Environment Slug: Required, Not Optional

Made `environment_slug` required. Tool description tells LLM: "For environment_slug, always use 'default'."

**Why:** YAGNI. We only have one environment. Optional parameter + fallback code for unused feature = unnecessary complexity. When we support multiple environments, update the tool and let LLM ask users.

### Stack Naming Convention

All resource names now include environment and workspace:

- **Base stacks:** `devopshero-{env}-vpc`, `devopshero-{env}-cluster`
- **App stacks:** `devopshero-{env}-{workspace}-{app}-ecr`, etc.
- **ECR repos:** `devopshero/{env}/{workspace}/{app}`

Prevents collisions when multiple workspaces deploy apps with the same slug.

### Worker Startup

Moved `RUN_DEPLOYMENT_WORKER` check to `settings.py` as `DOH_RUN_DEPLOYMENT_WORKER`. Django pattern: env → settings → code.

Import must be inside `ready()` because `deployment_worker` imports models, and models aren't ready at module load time. Django's `AppConfig.ready()` runs after all apps are loaded.

### Account-Scoped Environments (not Workspace-Scoped)

Initially considered Workspace-scoped environments (Workspace has many Environments). Changed the model during planning:

**Problems with workspace-scoped:**
- N workspaces × M environments = N×M VPCs and clusters (expensive, fragmented)
- "prod" would mean different things for each workspace

**Account-scoped is better:**
- "prod" means something org-wide — same network, same security posture, same compliance boundary
- Cost efficiency — shared VPC and cluster
- Networking — apps in same environment can communicate (same VPC)
- Simpler mental model — "deploy to prod" vs "deploy to workspace-X's prod"

**Implementation:** Environment has `aws_account` FK. Deployment references both `app` (→ Workspace) and `environment` (→ AWSAccount). See `docs/domain_model.md`.

---

## 2026-01-16 - Refactored AuroraClusterStack to Use Fn.importValue

Changed how AuroraClusterStack references the VPC from the base infrastructure.

**Problem:** The previous approach instantiated `VpcStack` inside the app deployment, which risked accidentally creating/updating the VPC when deploying an app. The comment said "it won't be deployed (already exists)" but CDK's `--all` flag would deploy it if it didn't exist.

**Initial fix:** Used `Vpc.from_lookup()` which does AWS API calls at synth time. This worked but created an extra `devopshero-vpc-lookup` CloudFormation stack and required `cdk.context.json` caching.

**Final solution:** Use CloudFormation's native `Fn.importValue` to reference exports from the VPC stack:
- Added AZ exports (`devopshero-az-1`, `devopshero-az-2`) to VpcStack in `deploy_base.py`
- AuroraClusterStack now imports VPC internally using `Vpc.from_vpc_attributes()` with `Fn.import_value()`
- Removed `vpc` and `default_security_group` parameters from AuroraClusterStack constructor

**Why this is better:**
- No extra CloudFormation stack
- No synth-time API calls
- Pure CloudFormation cross-stack references (battle-tested pattern)
- AuroraClusterStack is self-contained

---

## 2026-01-16 - Collapsible Tool Messages in Chat UI

Made tool call messages collapsible using native HTML `<details>`/`<summary>` elements.

**Why:** Tool messages show parameters and results which can be verbose. Collapsing them reduces visual clutter while keeping the info accessible.

**Implementation:**
- Used `<details>` with Tailwind's `group` class for state-based styling
- Chevron arrow rotates via `group-open:rotate-90` with smooth transition
- Hidden default marker with `list-none [&::-webkit-details-marker]:hidden`
- Tool start (spinner) opens expanded; completed tools collapse by default


---

## 2026-01-16 - CLI Test Harness for Main Agent

Built a CLI harness (`test_main_agent.py`) for fast agent iteration without the web UI.

**Why:** Going through the web page for every agent test is slow. Need to iterate quickly on prompts, resume from specific points, and branch conversations.

**Key Features:**
- **Session fork/resume:** Uses Claude Agent SDK's session management. `--conversation-id <uuid>` with `--fork` (default) branches the session; `--no-fork` resumes in place.
- **DB snapshotting:** Copies `db.sqlite3` to `test_db/` by default, so experiments don't pollute the main DB. Use `--no-copy` to persist state across runs.
- **REPL mode:** `--repl` for interactive back-and-forth.

**Design Decision — Parameter Clarity:** Initially had `resume_session_id` as a separate parameter flowing through `stream_response`. Refactored to use `conversation.session_id` as a carrier (set temporarily for fork scenarios). This eliminated redundant parameters:
- `stream_response(conversation, fork_session)` — uses `conversation.session_id` internally
- Fork case: new conversation gets source's `session_id` assigned (not saved) before calling `stream_response`

**Learning:** When forking, you create a NEW conversation but resume from the SOURCE's session. The conversation object can carry the session_id temporarily without persisting it — the agent will assign the new forked session_id after the response.

---

## 2026-01-16 - Fixed Spurious Error Logs for Non-Tool Blocks in Agent Service

Removed misleading error logs that fired when Claude responded with text only (no tool calls).

**The Issue:** `_handle_assistant_message` and `_handle_tool_results` in `agent_service.py` were logging errors when encountering `TextBlock` or non-`ToolResultBlock` content. These logs made it seem like something was wrong, but this is actually expected behavior.

**Why It's Expected:**
- Text is streamed via `SDKStreamEvent` objects during the response
- After streaming completes, an `AssistantMessage` arrives containing the full message (including `TextBlock`s)
- The `TextBlock` in `AssistantMessage` is redundant — text was already processed during streaming
- Similarly, `UserMessage` can contain non-tool-result blocks in certain SDK scenarios

**The Fix:** Replaced error logs with silent skips and explanatory comments:

```python
# In _handle_assistant_message:
if not isinstance(block, ToolUseBlock):
    # TextBlocks are expected here when Claude responds with text only.
    # The text has already been streamed via SDKStreamEvent, so we skip it.
    continue

# In _handle_tool_results:
if not isinstance(block, ToolResultBlock):
    # Non-ToolResultBlock content (e.g., TextBlock) can appear in synthetic
    # UserMessages from the SDK. These are informational and can be skipped.
    continue
```

**Learning:** When working with streaming SDKs, the final "complete" message often contains content that was already processed incrementally. Don't treat this as an error — it's just the SDK providing the assembled result.

---

## 2026-01-15 - Stable Message Input During Conversation Switch

Fixed the message input flickering when switching between conversations. Previously, the entire chat panel content was swapped via HTMX, causing the input field to disappear and reappear.

**Solution:** Used CSS View Transitions with named transition groups. Elements with the same `view-transition-name` on both sides of a transition morph smoothly instead of fading out/in with the rest.

**Changes:**
- `chat.html`: Added `transition:true` to the HTMX swap (`hx-swap="innerHTML transition:true"`)
- `_chat_panel.html`: Added `view-transition-name: chat-panel-content;` to the main container
- `_chat_panel.html`: Added `view-transition-name: message-input;` to the message input div
- `styles.css`: Added view transition animations (fast fade-out, slower fade-in)

**Bonus:** Changed scroll behavior from instant (`scrollToBottom`) to smooth (`scrollToBottomSmooth`) during SSE streaming events for a more polished feel.

**Why this works:** View Transitions match elements by their `view-transition-name`, not content. Even though the form's `hx-post` URL changes between conversations, the input appears stable because both old and new DOM have an element with `view-transition-name: message-input`.

---

## 2026-01-15 - Tool Call Parameters Visible During Execution

Enhanced tool call display to show parameters immediately when a tool starts executing, rather than waiting for completion. Also added consistent max-height constraints with scroll.

**Changes:**
- `_streaming_tool_start.html`: Now displays parameters section (was only showing tool name/spinner)
- `chat.py`: `_render_tool_start()` now passes `params_json` to the template
- All tool templates: Added max-height with overflow scroll to prevent long outputs from dominating the chat
  - Parameters: `max-h-96` (384px)
  - Results: `max-h-[32rem]` (512px)

**Files updated:** `_streaming_tool_start.html`, `_streaming_tool_result.html`, `_message_tool_call.html`, `chat.py`

---

## 2026-01-15 - Dynamic Conversation Title on Workspace Selection

When the agent selects a workspace, the conversation title now updates to "Working on {workspace_name}" in real-time.

**Implementation:**
- `select_workspace.py`: Sets `conversation.title` when pinning workspace
- `chat.py`: Added `_render_title_oob_swap()` to generate OOB swap HTML for title update when `select_workspace` succeeds
- `_chat_panel.html`: Added `id="conversation-title"` to header, plus `handleToolResult()` JS handler to process OOB swaps from tool results
- `chat.html`: Added `id="sidebar-title-{conv.id}"` to sidebar entries, plus `updateSidebarTitle()` JS to sync sidebar when main title changes

**Bug fixes:**
- `_enrich_tool_input()` now catches all exceptions (not just DoesNotExist) to handle invalid UUIDs gracefully
- Added system prompt guidance explaining that users refer to resources by name, and the agent should detect name vs UUID and look up UUIDs via list tools

---

## 2026-01-15 - Tool UI Improvements

Enhanced tool call display with DB-backed name resolution and consistent width.

**Name enrichment:** Added `_enrich_tool_input()` in `agent_service.py` to look up friendly names from the database before rendering. When `deploy_app` is called with `app_id`, we fetch the app name; same for `select_workspace` with `workspace_id`. This ensures the UI shows "Deploy App: my-cool-app" instead of a UUID.

**External tools:** Added `TOOL_MAIN_PARAMS` entries for Claude Agent SDK tools (Read → file_path, Shell/Bash → description).

**Width fix:** Tool call boxes weren't expanding to full width. Added `w-full` to the flex container so it expands before `max-w-[70%]` caps it. Applied to both streaming and non-streaming templates.

---

## 2026-01-15 - Tool Title Main Parameter Display

Enhanced tool call rendering to show the "main parameter" in the title for better scannability. For example, "Create Workspace: django-postgres-app" instead of just "Create Workspace".

**Implementation:**
- Added `TOOL_MAIN_PARAMS` mapping in `mcp_tools.py` linking tools to their primary parameter
- Added `get_tool_main_param()` to extract and format values (handles file URLs, UUIDs, seconds)
- Updated streaming templates and `chat.py` to pass both `tool_name` and `tool_main_param`
- Added `tool_display_name` and `tool_main_param` template filters for page-refresh rendering
- Main param rendered with lighter styling (`font-normal text-gray-500`) for visual hierarchy

**Tools with main params:** create_workspace (name), initiate_aws_connection (account_name), scan_repository (repo_url), create_app (name), create_datastore (name), wait (seconds)

**Tools without:** list_* tools (no params), select_workspace/deploy_app/get_deployment_status (only have UUIDs)

---

## 2026-01-15 - Replace whitenoise with servestatic

Fixed the Django ASGI warning about synchronous iterators in streaming responses.

**The Warning:**
```
StreamingHttpResponse must consume synchronous iterators in order to serve them asynchronously. Use an asynchronous iterator instead.
```

**Root Cause:** Not our SSE streaming code (which correctly uses async generators), but `whitenoise` middleware serving static files. Whitenoise uses synchronous file iteration internally, which triggers this warning under ASGI (uvicorn).

**Solution:** Replaced whitenoise with `servestatic`, an ASGI-native fork created specifically to address this issue. It's a drop-in replacement with identical configuration.

**Changes:**
- `pyproject.toml`: `whitenoise>=6.11.0` → `servestatic>=3.0.0`
- `settings.py`: `whitenoise.middleware.WhiteNoiseMiddleware` → `servestatic.middleware.ServeStaticMiddleware`

---

## 2026-01-15 - Friendly Tool Display Names

Added human-readable display names for MCP tools in the chat UI. Previously, tool calls showed the full MCP-namespaced names like `mcp__devopshero__list_aws_accounts`, which is an implementation detail users don't need to see.

**Solution:** Created a `TOOL_DISPLAY_NAMES` mapping in `mcp_tools.py` that maps full MCP names to properly capitalized labels (e.g., "List AWS Accounts", "Deploy App"). The `get_tool_display_name()` function performs the lookup and falls back to the full name for unknown tools.

**Why a mapping instead of string manipulation:** Using `removeprefix()` would just give `list_aws_accounts`, which is still technical. An explicit mapping allows proper capitalization and the flexibility to choose better names (e.g., "Initiate AWS Connection" vs "initiate_aws_connection").

---

## 2026-01-15 - Workspace Pinning and Repository Model Simplification

Redesigned the agent's workspace and repository handling to provide a cleaner mental model and prevent cross-workspace errors.

### The Problem

The existing design had several issues:
- Both `Workspace` and `App` had `repo_url` fields, creating ambiguity about which was the source of truth
- Tools like `create_app` required explicit `workspace_id` parameters, which could lead to mismatches
- No mechanism to "lock" a conversation to a workspace, risking accidental cross-workspace operations
- Two repository analysis tools (`inspect_repository` and the `repo-analyzer` sub-agent) with unclear differentiation

### Design Decisions

**One Workspace = One Repository = One App (v1)**
- Simplified the model: a workspace binds exactly one repository to an AWS account/region
- Removed `App.repo_url` — apps inherit from `workspace.primary_repo_url`
- Made `Workspace.primary_repo_url` required
- Future: add `repo_path` field for monorepo support

**Workspace Pinning**
- Once a workspace is selected for a conversation, it's immutable
- New `select_workspace` tool pins the workspace to `Conversation.workspace`
- Workspace-scoped tools (`create_app`, `create_datastore`) get workspace from conversation context, not parameters
- Platform tools (AWS connection, workspace creation) remain available in any conversation

**Repository Analysis Clarification**
- Renamed `inspect_repository` → `scan_repository` (quick, pattern-based)
- Renamed `repo-analyzer` sub-agent → `analyze-repository` (deep, LLM-powered)
- System prompt only mentions `analyze-repository`, biasing the agent toward thorough analysis
- Agent should analyze repository BEFORE creating workspace, to inform naming and configuration

### New Tools

- **`initiate_aws_connection`** — Creates pending AWS account, returns CloudFormation URL
- **`select_workspace`** — Pins workspace to conversation (fails if already pinned)
- **`list_workspaces`** — Lists all workspaces in organization

### Tool Changes

- **`create_app`** — Removed `workspace_id` and `repo_url` params; gets workspace from conversation
- **`create_datastore`** — Removed `workspace_id` param; gets workspace from conversation
- **`create_workspace`** — Now requires `primary_repo_url`, validates it's a `file://` URL

### Deployment Flow

The agent now follows this sequence:
1. `list_deployable_repos` — Show available repos
2. User selects a repo
3. `analyze-repository` sub-agent — Deep analysis before any decisions
4. Ask clarifying questions based on analysis
5. `list_aws_accounts` — Check connected accounts
6. `create_workspace` — Bind repo to AWS account/region
7. `select_workspace` — Pin to conversation
8. `create_app` — Configure build/runtime (no repo_url needed)
9. `create_datastore` — If analysis detected database needs
10. `deploy_app` — Initiate deployment


---

## 2026-01-14 - Repository Analysis Sub-Agent

Built an LLM-powered sub-agent that analyzes repositories to detect language, framework, service type, dependencies, and environment variables. Produces structured JSON with evidence for all claims.

**Key files created:**
- `repo_analysis/repo_analysis_schema.py` — Pydantic models for structured output (RepoAnalysisOutput, ServiceConfig, DependenciesConfig, EnvConfig, EvidenceItem)
- `repo_analysis/system_prompt.md` — Sub-agent prompt defining investigation strategy, evidence discipline, and output format
- `repo_analysis/repo_analyzer_config.py` — AgentDefinition with description, prompt, and allowed tools (Bash, Read, LS, Glob, Grep)
- `repo_analysis/test_repo_analysis.py` — CLI test harness with reference app expectations

**Integration:** Added sub-agent to main agent via `agents` parameter in `agent_service.py`. The main agent can invoke it via the SDK's native "Task" tool.

**Design decisions:**
- Uses SDK native sub-agents (not MCP tools) — cleaner invocation, automatic Task tool handling
- Sub-agent has restricted toolset (no Django models, no streaming, no clarifying questions)
- Evidence required for all major claims (file path + excerpt)
- One repo = one app (no monorepo support in v1)

**Test harness:** Validates against 8 reference apps (django_postgres_app, fastapi_app, nextjs_app, phoenix_app, etc.). Uses `query()` function for simple single-shot invocation.

```bash
uv run python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis --app fastapi_app
```

## 2026-01-13 - Chat Input History

Added up arrow key support to recall the last sent message, similar to terminal/shell behavior. Press up arrow when the input is empty to restore the previous message.

## 2026-01-13 - Streaming Markdown Rendering

Added real-time markdown rendering for chat messages using the `streaming-markdown` library (12KB, CDN).

**How it works:**
- **During streaming:** Text chunks are fed to `parser_write()` which renders markdown incrementally with append-only DOM updates
- **On page reload:** Messages stored as `ContentType.MARKDOWN` are rendered client-side using the same library for consistency

**Key files:**
- `chat_view.html` — Imports streaming-markdown, handles SSE events, renders stored markdown on load
- `_message_markdown.html` — Outputs raw markdown in `<script type="text/markdown">` for client-side rendering
- `styles.css` — Custom `.markdown-content` styles (headers, lists, code blocks, etc.) since `@tailwindcss/typography` not installed
- `agent_service.py` — Messages saved as `ContentType.MARKDOWN`
- `chat.py` — Streaming container uses `<div>` with `markdown-content` class

**Why not server-side rendering:** Considered `mistune` but using the same library client-side ensures identical output for streaming and page reload.

## 2026-01-13 - Fix Streaming Message Order and Tool Rendering

Fixed multiple issues with how messages appear during streaming vs after page reload.

### Problem 1: Messages Out of Order During Streaming

When the agent called a tool without producing text first, messages appeared in wrong order during streaming (text above tool box) but correct after reload (tool box above text).

**Root cause:** The initial `start` event created `#streaming-message` immediately. If the agent called a tool without text, this empty container sat above the tool box. After tool completion, a second `start` created another element with duplicate IDs. JavaScript's `getElementById` found the first (wrong) one, so text went to the container above the tool box.

**Fix:** 
- Only yield `start` when the first `text_delta` arrives (lazy creation)
- Added `has_started_streaming` flag to `StreamingContext`
- Yield `text_flush` only when there was actual streaming to flush

### Problem 2: Tool Results Not Rendered Fully

During streaming, tool boxes showed minimal status bars. After reload, they showed full details (Parameters + Result sections).

**Fix:** Updated `_render_tool_start()` and `_render_tool_result()` in `chat.py` to render the same rich HTML as `_message_tool_call.html` template, including:
- Agent avatar icon
- Parameters section with pretty-printed JSON
- Result section with pretty-printed JSON

### Problem 3: MCP Result Not Unwrapped

Tool results showed raw MCP wrapper `[{"type": "text", "text": "..."}]` during streaming but parsed content after reload.

**Fix:** Added `_extract_mcp_text_content()` helper in `chat.py` (mirrors `chat_filters.py`'s `json_pretty` logic) to unwrap MCP content blocks before display.

### Problem 4: Multiple "Thinking..." Indicators

When multiple tools ran back-to-back, empty streaming placeholders appeared between them.

**Fix:** Introduced `thinking` event type:
- Shows animated "Thinking..." indicator while waiting for agent response
- Uses dedicated `#thinking-indicator` placeholder with OOB swap (prevents duplicates)
- `start` event replaces thinking indicator when text begins
- `tool_start` event replaces thinking indicator when tool begins
- After each tool completes, thinking indicator reappears

### Problem 5: Redundant Typing Indicator

Had both "typing indicator" (shown on message send) and "thinking indicator" (shown during streaming).

**Fix:** Removed typing indicator entirely. The thinking indicator now serves as the single unified "waiting for agent" state.

---

## 2026-01-12 - Chat Streaming Architecture

Simplified the streaming architecture by eliminating the queue-based indirection. The SSE endpoint now runs the agent directly.

### Server Side

**Endpoints:**

- **`chat_send` (POST, sync)** — Creates user message in DB, returns user bubble HTML + typing indicator. Does not wait for agent.

- **`chat_stream` (GET, async)** — SSE endpoint. Runs forever in a loop:
  1. Load conversation from DB
  2. If last message is from user → run agent directly via `agent_service.stream_response()`
  3. Yield SSE events as they come from the generator
  4. Sleep 1s, send keepalive, repeat

**Agent streaming (`agent_service.stream_response`):**

An async generator that yields `StreamEvent` objects. Each event has a `type` and optional `data`:

- **`start`** — Begin new message container
- **`text_delta`** — Chunk of text to append (`{"text": "..."}`)
- **`text_flush`** — Finalize current text before tool execution
- **`tool_start`** — Tool execution beginning (`{tool_use_id, name, input}`)
- **`tool_result`** — Tool completed (`{tool_use_id, name, result, status, duration_ms}`)
- **`complete`** — Agent finished responding
- **`error`** — Something went wrong

Text accumulates in a local variable. On tool call or completion, accumulated text is persisted to DB as a Message.

### Client Side

**SSE connection** — HTMX SSE extension connects on page load:
```html
<div id="messages" hx-ext="sse"
     sse-connect="{% url 'chat_stream' ... %}"
     sse-swap="sse-start,sse-text-delta,...">
```

**Event handling** — Most events use HTMX's default swap (append HTML to `#messages`). Three events need JavaScript interception via `htmx:sseBeforeMessage`:

- **`sse-text-delta`** — Parsed as JSON, text appended to `#streaming-text` element (no DOM swap, just `textContent +=`)
- **`sse-text-flush`** — Removes cursor, clears `id` attributes so next `start` can create fresh elements
- **`sse-complete`** — Same as flush, plus removes `streaming-active` class

**UI element lifecycle:**

1. **`sse-start`** → Inserts `<div id="streaming-message">` with `<p id="streaming-text">` and blinking cursor. Also OOB-removes typing indicator.
2. **`sse-text-delta`** (repeated) → JS appends text to `#streaming-text`
3. **`sse-text-flush`** (optional, before tool) → Cursor removed, IDs cleared
4. **`sse-tool-start`** → Appends spinner HTML with `id="tool-{id}"`
5. **`sse-tool-result`** → OOB replaces `#tool-{id}` with completion status
6. **`sse-start`** (after tool) → Creates new streaming container for post-tool text
7. **`sse-complete`** → Final cleanup, cursor removed

**OOB (Out-of-Band) swaps** — Used for targeted replacements outside the main append flow:
- Typing indicator removal: `<div id="typing-indicator" hx-swap-oob="outerHTML"></div>` (replaces with empty div)
- Tool result: `<div id="tool-{id}" hx-swap-oob="outerHTML">...</div>` (replaces spinner with result)

**Key files:**
- `devopshero_app/views/chat.py` — Endpoints + SSE formatting
- `devopshero_app/services/agent/agent_service.py` — Agent generator
- `devopshero_app/templates/devopshero_app/chat/chat_view.html` — Client JS

---

## 2026-01-11 - Remove ask_user Tool and AskUserQuestion Handling

**Decision:** Removed the `ask_user` MCP tool and related `AskUserQuestion` handling to simplify the codebase before adding new features.

**What was removed:**
- `devopshero_app/services/agent/tools/ask_user.py` — The tool implementation (128 lines)
- `_convert_ask_user_question_to_choice()` in agent_service.py — Converted Claude Code's built-in AskUserQuestion to CHOICE messages
- `_extract_deferred_choice()` in agent_service.py — Extracted deferred choice data from tool results
- All special-case handling for ask_user in the message processing loop

**Why:** The ask_user tool added significant complexity:
- Required special handling to create CHOICE messages with correct ordering
- Had input normalization bugs (JSON strings vs dicts)
- Needed deferred_choice pattern to fix message ordering issues
- Created maintenance burden with two code paths for user questions (our tool + Claude's built-in)

**Current state:** The agent can no longer programmatically present interactive choice buttons to users. If the model needs user input, it must ask in natural language and wait for a text response. The CHOICE message type and `_message_choice.html` template remain in the codebase but are currently unused.

**Future consideration:** If interactive choices are needed again, consider a simpler approach or rely on Claude Code's built-in `AskUserQuestion` tool (which would appear as a TOOL_CALL card rather than custom UI).

---

## 2026-01-11 - Fix Chat Message Ordering for ask_user Tool

**Problem:** When Claude called the `ask_user` tool, messages appeared in wrong order: CHOICE buttons first, then TOOL_CALL, then TEXT. The model's past-tense response ("I've asked...") appeared before the actual question.

**Root cause:** Messages ordered by `created_at`. The `ask_user` tool created CHOICE messages during tool execution (early timestamp), while TEXT was created after stream completed (late timestamp).

**Solution:**
- Moved CHOICE creation out of `ask_user` tool — now returns `deferred_choice` data
- `agent_service` collects all tool messages during stream, creates them in correct order after stream ends
- Final order: TOOL_CALL → CHOICE → TEXT (model commentary last)

**Refactoring:**
- Extracted `_extract_deferred_choice()` helper for parsing tool results
- Renamed `deferred_messages` → `tool_messages`
- Reduced indentation via early `continue` statements

---

## 2026-01-11 - Display Tool Calls in Conversation UI

Added visibility into agent tool calls. Previously, users only saw final text responses with no indication of what tools were called or what they returned.

**Implementation:**
- Added `TOOL_CALL` content type to Message model
- Capture `ToolUseBlock` and `ToolResultBlock` from Claude SDK response stream
- Match tool invocations to results by `tool_use_id`, calculate duration
- New `_message_tool_call.html` template with expanded display (tool name, params, result, duration)
- `json_pretty` filter that extracts JSON from MCP content blocks `[{"type": "text", "text": "..."}]`

**Bug fixes:**
- `select_related('organization')` on conversation fetch to avoid lazy load in async context
- Replace `.alist()` with `async for` iteration — `alist()` doesn't exist in Django 6.0 (confirmed via docs)

---

## 2026-01-11 - Phase 3: Deployment Flow Agent Tools

Implemented the deployment flow tools for the AI agent (OpenSpec add-deployment-agent Phase 3).

**New tools (5):**
- `create_workspace` — Creates workspaces with AWS account/region config
- `create_app` — Creates app configs with container/build settings
- `create_datastore` — Creates Aurora Serverless v2 database configs
- `deploy_app` — Creates deployment records (stubbed in v1, no real infra)
- `get_deployment_status` — Returns deployment status, phase, and logs

**Progress indicator enhanced:**
- Phase step visualization: Init → Build → Push → Deploy → Health → Done
- Green checkmark and styling when complete
- Started timestamp display

**Process note:** Initially wrote tools as sync functions using `sync_to_async` wrapper, but AGENTS.md specifies using Django's native async ORM methods (`aget`, `acreate`, `aexists`, etc.). Fixed CLAUDE.md by symlinking it to AGENTS.md so patterns are always in context.

---

## 2026-01-10 - Fix Newlines in Chat Messages

**Problem:** Newlines in agent responses weren't displaying - everything appeared on one line.

**Root causes:**
1. SSE was collapsing newlines with `.replace("\n", "")` for single-line format
2. Agent messages used MARKDOWN content type, which expected pre-rendered HTML
3. User messages lacked `whitespace-pre-wrap`

**Fixes:**
- SSE now uses proper multi-line format: each line prefixed with `data:`
- Agent messages use TEXT content type (has `whitespace-pre-wrap`)
- Added `whitespace-pre-wrap` to user messages and fallback
- Increased font size (removed `text-sm`)

---

## 2026-01-10 - Chat UI Polish: Scrollbar & Layout

Refined the chat interface to match ChatGPT/Claude patterns.

**Scrollbar Styling:**
- Dark scrollbar (`gray-700`) with transparent track
- 12px width for visibility
- Uses both `scrollbar-color` (Firefox) and `::-webkit-scrollbar` (Chrome/Safari)

**Layout - Full-width scroll, centered content:**
- Outer container extends full width (negative margins) so scrollbar is at viewport edge
- Inner content constrained to `max-w-4xl` (896px) and centered
- Header, messages, and input all follow same pattern

**Auto-scroll:**
- Scrolls to bottom on page load
- Scrolls on `htmx:afterSwap` (form submission) and `htmx:sseMessage` (SSE)

**Model Change:**
- Switched from Opus 4.5 to Sonnet 4 for faster responses

---

## 2026-01-10 - SSE Chat Streaming Implementation

Implemented real-time chat message streaming using HTMX SSE extension.

### Key Changes

**SSE Extension Setup (`base.html`):**
- Added `htmx-ext-sse@2.2.4` from CDN with `defer` attribute (must load after HTMX)
- Initial bug: extension loaded before HTMX causing "htmx is not defined" error

**SSE Event Format (`chat.py`):**
- Event name: `new-chat-message` (more descriptive than generic "message")
- HTML must be single-line for SSE: `html.replace("\n", "").strip()`
- Only stream agent/system messages; user messages handled by form submission

**Typing Indicator (`_typing_indicator.html`, `view.html`):**
- Uses OOB swap with `hx-swap-oob="outerHTML"` to replace a placeholder div
- On agent response, placeholder is restored (not deleted) for reuse: `<div id="typing-indicator" hx-swap-oob="outerHTML"></div>`
- Positioned outside `#messages` div but inside `#messages-container` so it always appears at bottom

**Empty Chat Placeholder:**
- Given ID `empty-chat-placeholder`
- Deleted via OOB swap on first message sent

**Layout (`view.html`):**
- ChatGPT-style layout: `h-[calc(100vh-9rem)]` fills viewport minus header/padding
- Messages container uses `flex-1` with `overflow-y-auto`
- Input fixed at bottom

### Gotchas

- **Script load order:** SSE extension needs `defer` to load after HTMX
- **Multi-line SSE data:** Each line needs `data:` prefix, or collapse to single line
- **OOB delete vs clear:** Using `delete` removes element entirely; subsequent OOB swaps fail. Use `outerHTML` with empty div to preserve placeholder.
- **Duplicate messages:** Form submission + SSE both showed user messages. Fixed by skipping USER role in SSE stream.

---

## 2026-01-10 - HTMX Sidebar Optimization & History Navigation Fix

Fixed two issues with the HTMX-based SPA navigation.

### Problem 1: Wasteful Sidebar OOB Updates

Every page navigation was sending the **entire sidebar HTML twice** (mobile + desktop) via HTMX out-of-band swaps — roughly 2-3KB per click — just to update which nav item has the "active" highlight class.

**Before:** Each page template included `_sidebar_oob.html`:
```html
<div id="sidebar-nav-desktop" hx-swap-oob="true">{% include "_sidebar_nav.html" %}</div>
<div id="sidebar-nav-mobile" hx-swap-oob="true">{% include "_sidebar_nav.html" %}</div>
```

**After:** Removed all OOB includes. Added 10 lines of client-side JS that updates nav highlighting based on `location.pathname`:
```javascript
function updateNavHighlight() {
    document.querySelectorAll('.nav-link').forEach(link => {
        const isActive = location.pathname.startsWith(link.dataset.navUrl);
        link.classList.toggle('bg-white/5', isActive);
        link.classList.toggle('text-white', isActive);
        link.classList.toggle('text-gray-400', !isActive);
    });
}
document.body.addEventListener('htmx:pushedIntoHistory', updateNavHighlight);
window.addEventListener('popstate', updateNavHighlight);
```

The server-side `is_active` logic remains for the initial page render; JS only handles subsequent HTMX navigations.

### Problem 2: Browser Back/Forward Didn't Work

Clicking links worked, but the browser back/forward buttons did nothing — content didn't restore and nav highlighting didn't update.

**Root cause:** This is an SPA-style app where only `#main-content` changes. By default, HTMX tries to snapshot/restore the entire `<body>` for history navigation, which doesn't work well for shell-based layouts.

**Fix:** Added `hx-history-elt` to the main content div:
```html
<div id="main-content"
     hx-history-elt
     hx-get="{{ content_url }}"
     hx-trigger="load"
     hx-swap="innerHTML">
```

This tells HTMX: "This element is the page content. Snapshot and restore just this element for history navigation."

Also added `popstate` listener (see JS above) so nav highlighting updates on back/forward.

### Key Insight

HTMX history has two parts:
- **`hx-push-url`** — pushes URL to browser history (we had this)
- **`hx-history-elt`** — tells HTMX which element to snapshot/restore (we were missing this)

Without `hx-history-elt`, HTMX doesn't know what content represents the "page" in a shell-based SPA.

**Files changed:**
- `app_shell.html` — added `hx-history-elt` to `#main-content`
- `partials/_sidebar_nav.html` — added client-side nav highlighting JS
- Removed `partials/_sidebar_oob.html`
- Removed `{% include "_sidebar_oob.html" %}` from 8 page templates

---

## 2026-01-10 - Claude Agent Backend Configuration

The deployment agent supports two Claude backends with automatic selection:

**Priority:** `ANTHROPIC_API_KEY` > `AWS_BEDROCK_REGION`

- If `ANTHROPIC_API_KEY` is set → direct Anthropic API (model: `claude-opus-4-5-20251101`)
- Else if `AWS_BEDROCK_REGION` is set → AWS Bedrock (model: `anthropic.claude-opus-4-5-20251101-v1:0`)
- Else → agent unavailable (`is_available()` returns `False`)

Bedrock auto-discovers AWS credentials from CLI/environment. No default region—must be explicitly configured.

**Key files:** `devopshero_app/services/agent/client.py`

---

## 2026-01-10 - Phase 1: Deployment Agent Foundation

Implemented the foundation layer for the AI deployment agent—a conversational interface that will guide users through deploying their applications.

### What We Built

**Django Models:**
- **Workspace, App, Datastore** — Core entities representing deployed resources
- **Deployment, DeploymentLog** — Track deployment attempts and their progress
- **Conversation, Message** — Chat history between user and agent

**Chat Interface:**
- `chat_list`, `chat_view`, `chat_new` — Navigation and conversation management
- `chat_send` — POST endpoint for user messages
- `chat_stream` — SSE endpoint for real-time agent responses
- `chat_messages`, `chat_close` — Pagination and conversation lifecycle

**Message Partials:**
- 10 templates for different content types: text, markdown, code, progress bars, choice buttons, deployment logs, errors
- Typing indicator for agent "thinking" state

### Why

The deployment agent will be a chat-based interface where users describe what they want to deploy, and the agent analyzes their repository, suggests configurations, and orchestrates the deployment. Phase 1 provides:

1. **Persistence** — Conversations survive page refreshes and server restarts
2. **Real-time updates** — SSE allows the agent to stream responses without polling
3. **Rich content** — Different message types enable progress indicators, code blocks, and interactive choices

### What's Next

- Phase 2: Agent core (system prompt, LLM integration, tool definitions)
- Phase 3: Stubbed tool implementations (analyze repos, create deployments)
- Phase 4: UI integration (dashboard button, full chat layout)

---

## 2026-01-08 22:14 - Aurora DatabaseConfig Implementation

- **Scope:** Implemented the Aurora-only `DatabaseConfig` spec in `infra_customer/`.
- **Key changes:** Added nested config dataclasses, engine/version helpers, and per-app Aurora stack/secret naming.
- **Secrets:** Added derived connection secret with `DATABASE_URL` plus individual fields, injected via ECS Secrets Manager.
- **Deployment:** Supports serverless v2 and provisioned modes with orthogonal config validation.

## 2026-01-08 - db-portal Deployment Fix: Migrations & Monitoring

### The Problem

db-portal deployment started failing with tasks crashing immediately:

```
Tasks: 1/1 running, 0 pending
⏳ Confirming stability (2 more checks)...
Tasks: 0/1 running, 0 pending
⚠️  Tasks are failing:
   ❌ Essential container in task exited
```

### Diagnosis

Used AWS CLI to investigate:

```bash
# Check service status
aws ecs describe-services --cluster devopshero-cluster --services db-portal

# List stopped tasks
aws ecs list-tasks --cluster devopshero-cluster --service-name db-portal --desired-status STOPPED

# Describe task to get stop reason
aws ecs describe-tasks --cluster devopshero-cluster --tasks <task-id>

# Get CloudWatch logs
aws logs get-log-events --log-group-name /devopshero/ecs --log-stream-name db-portal/db-portal/<task-id>
```

**Root cause from logs:**

```
** (MyXQL.Error) (1146) (ER_NO_SUCH_TABLE) Table 'db_portal_prod.digests' doesn't exist
```

The Aurora database was freshly provisioned but **no migrations had ever been run**. The app crashed on startup trying to query the `digests` table.

### Fix 1: Run Migrations on Container Start

Modified `Dockerfile` to run migrations before starting the app:

```dockerfile
# Before
CMD ["bin/db_portal", "start"]

# After
CMD ["sh", "-c", "bin/db_portal eval 'DbPortal.Release.migrate()' && bin/db_portal start"]
```

This uses Elixir's release eval to run the `DbPortal.Release.migrate/0` function (which calls `Ecto.Migrator.run/3`) before starting the Phoenix server.

### Fix 2: Monitoring Was Reporting Old Failures

After fixing migrations, the deployment still reported failures—but the service was actually running fine! The monitoring code had a bug.

**Problem:** `check_stopped_tasks()` in `ecs_utils.py` was querying ALL stopped tasks for the service, including old failed tasks from previous deployment attempts. With 15+ old crashed tasks sitting around, it kept reporting them as current failures.

**Fix:** Added `deployment_start_time` parameter to filter out old tasks:

```python
def check_stopped_tasks(ecs_client, cluster, service, deployment_start_time):
    # ...
    for task in details["tasks"]:
        # Skip tasks that started before our deployment (old failures)
        task_started_at = task.get("startedAt")
        if task_started_at and task_started_at < deployment_start_time:
            continue
        # ... rest of failure checking
```

The deployment start time is recorded in `start_ecs_service()` before triggering the deployment, and passed through to the monitoring functions.

### Resetting the Database for Testing

Needed to drop all tables to test migrations from scratch. Multiple approaches failed before finding what works.

**Success: DROP DATABASE + CREATE DATABASE**
```bash
aws ecs run-task --cluster devopshero-cluster --task-definition devopshero-db-portal \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=DISABLED}' \
  --overrides '{
    "containerOverrides": [{
      "name": "db-portal",
      "command": ["sh", "-c", "bin/db_portal eval '\''Application.load(:db_portal); {:ok, _, _} = Ecto.Migrator.with_repo(DbPortal.Repo, fn repo -> repo.query!(\"DROP DATABASE db_portal_prod\"); repo.query!(\"CREATE DATABASE db_portal_prod\"); IO.puts(\"Database dropped and recreated\") end)'\''"]
    }]
  }'
```

This worked because:
- `Ecto.Migrator.with_repo/2` properly starts the Repo with all config
- `DROP DATABASE` + `CREATE DATABASE` is atomic and bypasses FK issues
- The brief connection error mid-execution (when DB is dropped) is harmless


---

## 2026-01-07 - CDK Cleanup: Deprecation Warning and Notices

Fixed two CDK CLI annoyances:
- **Deprecation warning:** Replaced `container_insights=True` with `container_insights_v2=ecs.ContainerInsights.ENABLED` in `deploy_base.py`
- **Telemetry notice:** Added `--no-notices` flag to CDK deploy command in `cdk_utils.py`

---

## 2026-01-07 - Unified Deployment Entry Point

Refactored deployment to separate base layer from app deployment, with a single entry point.

**Key changes:**
- Created `deploy.py` — unified entry point for all deployments
- Created `app_configs.py` — registry of all app configurations
- Split deployment into separate modules: `deploy_base.py`, `deploy_app.py`, `cdk_utils.py`
- Deleted `deploy_app_simple_dashboard.py` and `deploy_app_db_portal.py`
- Base layer (VPC + ECS cluster) is now deployed explicitly, not as part of app deployment

**New file structure:**
- `deploy.py` — CLI entry point
- `app_configs.py` — app configuration registry
- `deploy_base.py` — VPC/ECS cluster stacks and deployment
- `deploy_app.py` — app stacks (ECR, ALB, ECS service, Aurora)
- `cdk_utils.py` — shared CDK deployment utilities

**New usage:**
```bash
# Base layer
uv run python deploy.py --base                       # Deploy VPC + ECS cluster
uv run python deploy.py --base --teardown            # Teardown base layer

# Apps
uv run python deploy.py --app simple-dashboard       # Deploy app
uv run python deploy.py --app db-portal              # Deploy another app
uv run python deploy.py --app simple-dashboard --image-tag v1.2.3  # Specific tag
uv run python deploy.py --app simple-dashboard --teardown          # Teardown app only
```

---

## 2026-01-07 - Deprecated CloudFormation Deployment Engine

Moved all CloudFormation-based deployment code to `infra_customer/_old_cf/`:
- `deploy_app_cf.py` — CloudFormation deployment module
- `cf_templates/` — All CloudFormation JSON templates

**Changes:**
- CDK is now the only deployment engine
- `cloudformation_utils.py` stays in place (still used by CDK for stack operations)

---

## 2026-01-08 - Aurora Credentials via ECS Secret Injection

All 5 database fields now come from Aurora's managed secret via ECS secret injection:

```python
secrets["DATABASE_HOST"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="host")
secrets["DATABASE_PORT"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="port")
secrets["DATABASE_NAME"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="dbname")
secrets["DATABASE_USERNAME"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="username")
secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="password")
```

Previously `host`, `port`, `dbname` were plain env vars. Now all DB credentials stay in Secrets Manager.

---

## 2026-01-07 - App Secrets Architecture: Per-App Isolation with boto3

Implemented a secrets management system that provides per-app isolation and flexible secret structures.

### Why Not CloudFormation for Secrets?

CloudFormation's `GenerateSecretString` can only auto-generate **ONE** random field per secret (the parameter is `generate_string_key: str`, not a list). This was too limiting for apps like db_portal that need multiple generated fields:

- `secret_key_base` — Phoenix secret key base (needs to be random)
- `signing_salt` — Phoenix signing salt (needs to be random)
- `slack_token` — Slack API token (literal value)

### Solution: boto3 Secret Creation Before CDK

Secrets are created via boto3 **before** CDK runs, allowing:
- Multiple randomly-generated fields per secret
- Custom JSON structure per app
- Secrets persist across stack deletions (feature, not bug)

**Flow:**
1. `deploy()` calls `secrets_utils.ensure_app_secrets_exist()`
2. If `devopshero/{app_name}/secrets` doesn't exist → generate values for `None` fields → create via boto3
3. If it exists → leave it alone (values are stable)
4. CDK only grants IAM permissions to read the secret (no secret creation in CloudFormation)

### Per-App Task Role Isolation

Each app gets its own ECS task role with permissions scoped to only its secrets:

```python
# AppWithAlbStack creates per-app task role
task_role = iam.Role(self, "TaskRole", role_name=f"devopshero-{app_name}-task-role", ...)
task_role.add_to_policy(iam.PolicyStatement(
    actions=["secretsmanager:GetSecretValue"],
    resources=[f"arn:aws:secretsmanager:{region}:{account}:secret:devopshero/{app_name}/*"],
))
```

This ensures `db-portal` cannot read `simple-dashboard` secrets, and vice versa.

### AppConfig Secret Definition

Apps define their secret structure in `AppConfig`:

```python
app_secrets={
    "slack_token": "disabled",   # Literal value
    "secret_key_base": None,     # Generate random 64-char
    "signing_salt": None,        # Generate random 64-char
}
```

- `str` value → use literally
- `None` → generate random 64-char alphanumeric string

### App-Side: SecretsManagerConfigProvider

The Elixir app reads secrets from `devopshero/{app_name}/secrets`:

```elixir
# lib/db_portal/secrets_manager_config_provider.ex
defp load_secrets(secret_name) do
  with {:ok, %{"SecretString" => json}, _} <- Aws.get_secret_value(secret_name),
       {:ok, secrets} <- Jason.decode(json) do
    {:ok, %{
      "signing_salt" => Map.get(secrets, "signing_salt", ""),
      "secret_key_base" => Map.get(secrets, "secret_key_base", ""),
      :slack_token => Map.get(secrets, "slack_token", "disabled")
    }}
  end
end
```

### Dev Mode Bypass

In dev, set `:no_secrets_mgr` in `config/dev.exs` to skip AWS:

```elixir
config :db_portal, :no_secrets_mgr,
  slack_token: "dev-slack-token",
  signing_salt: "dev-signing-salt",
  secret_key_base: "dev-secret-key-base-64-chars..."
```

### Key Files

- `infra_customer/secrets_utils.py` — `ensure_app_secrets_exist()` function
- `infra_customer/appconfig.py` — `app_secrets` field definition
- `infra_customer/deploy_app_cdk.py` — Per-app task role with scoped IAM
- `db_portal/lib/db_portal/secrets_manager_config_provider.ex` — App-side secret loading
- `db_portal/lib/db_portal/application.ex` — Dev/prod branching via `:no_secrets_mgr`

---

## 2026-01-07 - ARM64 Docker Builds: Avoiding QEMU Emulation Bug with Elixir 1.18

When building Docker images for ECS/Fargate on Apple Silicon Macs, we encountered a critical build failure with Elixir 1.18.

**The Error:**
```
Error while loading project :configparser_ex at /app/deps/configparser_ex
** (ArgumentError) could not call Module.put_attribute/3 because the module DbPortal.MixProject is already compiled
```

**Root Cause:** Building with `--platform linux/amd64` on an ARM Mac forces Docker to use QEMU emulation. QEMU has subtle timing/behavior differences that expose a bug in Elixir 1.18's module loading during `mix deps.compile`. The error occurs because Mix tries to put an attribute on a module that's already been compiled — a race condition that only manifests under emulation.

**Solution:** Build for ARM64 and run on Graviton (ARM) Fargate instances:

```python
# ecr_utils.py - build_and_push_docker_image()
build_result = subprocess.run(
    ["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "."],
    ...
)

# deploy_app_cdk.py - FargateTaskDefinition
runtime_platform=ecs.RuntimePlatform(
    cpu_architecture=ecs.CpuArchitecture.ARM64,
    operating_system_family=ecs.OperatingSystemFamily.LINUX,
),
```

**Why ARM64 is Better:**
- **Native builds on Apple Silicon** — no QEMU emulation, no timing bugs
- **20% cheaper** — Graviton instances cost less than x86
- **Better performance** — Graviton2/3 processors are fast

If amd64 builds are ever needed (e.g., for x86 Fargate), consider:
- Downgrading to Elixir 1.17.x or earlier
- Using a CI/CD system with native x86 runners (GitHub Actions, etc.)
- Building on an x86 machine or EC2 instance

---

## 2026-01-07 - db_portal Changes for ECS/Fargate Deployment

Made several modifications to `db_portal` (Elixir/Phoenix app) to run in ECS/Fargate behind an ALB. The app was originally designed to run on EC2 with direct HTTPS and Okta SSO.

### 1. Health Check Endpoint

**Files:** `lib/db_portal_web/router.ex`, `lib/db_portal_web/controllers/health_controller.ex`

Added a dedicated `/health` endpoint for ALB health checks that bypasses authentication:

```elixir
# router.ex - Add before authenticated routes
scope "/health", DbPortalWeb do
  get "/", HealthController, :index
end

# health_controller.ex - New file
defmodule DbPortalWeb.HealthController do
  use DbPortalWeb, :controller
  def index(conn, _params) do
    send_resp(conn, 200, "OK")
  end
end
```

### 2. HTTP-Only Mode (DISABLE_HTTPS)

**File:** `config/runtime.exs`

The app originally ran its own HTTPS server with SiteEncrypt/ACME. Behind ALB (which terminates TLS), we need HTTP-only mode:

```elixir
if System.get_env("DISABLE_HTTPS") == "true" do
  http_port = String.to_integer(System.get_env("PORT", "4000"))
  host = System.get_env("PHX_HOST", "localhost")

  config :db_portal, DbPortalWeb.Endpoint,
    url: [host: host, port: 443],  # External URL (ALB terminates TLS)
    http: [port: http_port],        # Internal port for ALB health checks
    server: true,
    check_origin: false
end
```

### 3. Authentication Bypass (DISABLE_AUTH)

**File:** `config/runtime.exs`

The app uses Okta SAML for production auth. For ECS deployment without Okta configured, we can bypass:

```elixir
use_okta_auth = cond do
  System.get_env("DISABLE_AUTH") == "true" -> false  # NEW: Explicit bypass
  Application.get_env(:db_portal, :env) == :prod -> true
  System.get_env("MY_OKTA") != nil -> true
  true -> false
end
```

When disabled, the app uses `do_fake_verify` which creates a session for `dev@example.com`.

### 4. Secrets Manager Bypass (NO_SECRETS_MGR)

**Files:** `config/runtime.exs`, `lib/db_portal/application.ex`

The app reads secrets (Slack token, signing salt, secret key base) from AWS Secrets Manager. For ECS, we inject these via environment variables:

```elixir
# runtime.exs
if System.get_env("NO_SECRETS_MGR") == "true" do
  config :db_portal, :no_secrets_mgr,
    slack_token: System.get_env("SLACK_TOKEN", "disabled"),
    signing_salt: System.get_env("SIGNING_SALT", "default-signing-salt-change-me"),
    secret_key_base: System.get_env("SECRET_KEY_BASE", "...")
end

# application.ex - configuration/1 function
case Application.get_env(:db_portal, :no_secrets_mgr) do
  nil -> # Use Secrets Manager (original behavior)
  conf -> # Use env vars from :no_secrets_mgr config
end
```

### 5. Flexible Database Configuration

**File:** `config/runtime.exs`

Support both `DATABASE_URL` (traditional) and individual components (for ECS secrets injection):

```elixir
cond do
  database_url = System.get_env("DATABASE_URL") ->
    config :db_portal, DbPortal.Repo, url: database_url, pool_size: ...

  System.get_env("DATABASE_HOST") ->
    config :db_portal, DbPortal.Repo,
      hostname: System.get_env("DATABASE_HOST"),
      port: String.to_integer(System.get_env("DATABASE_PORT", "3306")),
      database: System.get_env("DATABASE_NAME", "db_portal_prod"),
      username: System.get_env("DATABASE_USERNAME", "dbadmin"),
      password: System.get_env("DATABASE_PASSWORD", ""),
      pool_size: ...

  true -> :ok  # Use defaults from dev.exs/prod.exs
end
```

### Environment Variables Summary

| Variable | Purpose | Example Value |
|----------|---------|---------------|
| `DISABLE_HTTPS` | Run HTTP-only (ALB terminates TLS) | `true` |
| `DISABLE_AUTH` | Bypass Okta SSO | `true` |
| `NO_SECRETS_MGR` | Use env vars instead of Secrets Manager | `true` |
| `PORT` | HTTP listen port | `4000` |
| `PHX_HOST` | External hostname | `dataengr.chsandbox.com` |
| `DATABASE_HOST` | Aurora endpoint | `devopshero-aurora.cluster-xxx.rds.amazonaws.com` |
| `DATABASE_PORT` | MySQL port | `3306` |
| `DATABASE_NAME` | Database name | `db_portal_prod` |
| `DATABASE_USERNAME` | DB user (from Secrets Manager) | Injected by ECS |
| `DATABASE_PASSWORD` | DB password (from Secrets Manager) | Injected by ECS |
| `SECRET_KEY_BASE` | Phoenix secret key | 64+ char string |
| `SIGNING_SALT` | Cookie signing salt | Random string |

---

## 2026-01-06 - Fixed False Failures in ECS Service Stability Check

The `wait_for_service_stable` function was incorrectly reporting task failures during successful deployments.

**Symptom:** Service would reach stable state (1/1 running, 0 pending) but then fail with:
```
⚠️  Tasks are failing:
   ❌ Scaling activity initiated by (deployment ecs-svc/...)
❌ Too many task failures, aborting
```

**Root Cause:** The `check_stopped_tasks` function was treating **all** stopped tasks as failures. During normal deployments, ECS stops old tasks with `stopCode: ServiceSchedulerInitiated` — this is expected behavior (old tasks being rotated out), not a failure.

**Fix:** Updated `check_stopped_tasks` to check the `stopCode` field and ignore intentional stops:

```python
INTENTIONAL_STOP_CODES = {
    "ServiceSchedulerInitiated",  # Normal deployment/scaling rotation
    "UserInitiated",              # User manually stopped the task
    "SpotInterruption",           # Spot instance interrupted (not app's fault)
}

# Skip tasks that were intentionally stopped (not failures)
if stop_code in INTENTIONAL_STOP_CODES:
    continue
```

Now only genuine failures (`EssentialContainerExited`, `TaskFailedToStart`, etc.) are reported.

---

## 2026-01-06 - Fixed CDK Subnet Route Table Warnings

CDK emits warnings when importing a VPC without route table IDs:

```
[Warning at .../ImportedVpc/PublicSubnet1] No routeTableId was provided to the subnet...
```

**Attempted workarounds (rejected):**
- `@aws-cdk/aws-ec2:noSubnetRouteTableId` context flag — only *acknowledges* the warning, doesn't suppress output
- Filtering stderr — works but feels hacky

**Fix:** Export route table IDs from `VpcStack` and import them in `AppWithAlbStack`. With `nat_gateways=1`, all public subnets share one route table and all private subnets share one route table, so we only need two exports:

```python
# VpcStack exports
CfnOutput(self, "PublicRouteTableId", value=self.vpc.public_subnets[0].route_table.route_table_id, ...)
CfnOutput(self, "PrivateRouteTableId", value=self.vpc.private_subnets[0].route_table.route_table_id, ...)

# AppWithAlbStack imports (same RT repeated for each subnet)
vpc = ec2.Vpc.from_vpc_attributes(
    ...,
    public_subnet_route_table_ids=[public_rt, public_rt],
    private_subnet_route_table_ids=[private_rt, private_rt],
)
```

---

## 2026-01-06 - CDK Deployment Fixes (AZs, CIDR Selection, Output Directory)

Fixed several issues preventing CDK deployments from working correctly.

### Issue 1: Dummy Availability Zones

**Problem:** CDK was using `dummy1a` and `dummy1b` instead of real availability zones like `us-east-1a`. This caused CloudFormation to fail with "Value (dummy1a) for parameter availabilityZone is invalid."

**Root Cause:** When CDK synthesizes stacks for cross-account deployment, it doesn't have context about the target account's AZs and falls back to dummy values.

**Fix:** Added explicit `availability_zones` parameter to `VpcStack`:

```python
class VpcStack(Stack):
    def __init__(self, ..., availability_zones: list[str], ...):
        self.vpc = ec2.Vpc(
            self, "Vpc",
            availability_zones=availability_zones,  # Explicit AZs
            ...
        )

# In deploy():
availability_zones = [f"{region}a", f"{region}b"]
vpc_stack = VpcStack(..., availability_zones=availability_zones, ...)
```

### Issue 2: EcsClusterStack Creating Its Own VPC

**Problem:** `ecs.Cluster()` creates a default VPC if none is provided, which also had the dummy AZ problem.

**Fix:** Modified `EcsClusterStack` to accept a VPC parameter:

```python
class EcsClusterStack(Stack):
    def __init__(self, ..., vpc: ec2.IVpc, ...):
        self.cluster = ecs.Cluster(
            self, "EcsCluster",
            vpc=vpc,  # Use provided VPC instead of creating one
            ...
        )

# In deploy():
ecs_cluster_stack = EcsClusterStack(..., vpc=vpc_stack.vpc, ...)
ecs_cluster_stack.add_dependency(vpc_stack)
```

### Issue 3: Hardcoded VPC CIDR

**Problem:** VPC CIDR was hardcoded as `172.21.0.0/20`, which could conflict with existing VPCs.

**Fix:** Reused the CloudFormation approach — check if VPC stack exists, otherwise find available CIDR:

```python
vpc_stack_exists = cloudformation_utils.stack_exists(cf_client, vpc_stack_name)

if vpc_stack_exists:
    vpc_cidr = cloudformation_utils.get_stack_output(cf_client, stack_name=vpc_stack_name, output_key="VpcCidr")
else:
    cidr_config = vpc_utils.find_available_vpc_cidr(ec2_client)
    vpc_cidr = cidr_config["VpcCidr"]
```

### Issue 4: CDK Output Directory

**Problem:** `app.synth()` was writing to `cdk.out/` in whatever directory the script ran from.

**Fix:** Configured explicit output directory in `infra_customer/cdk.out/`:

```python
CDK_OUT_DIR = Path(__file__).parent / "cdk.out"
cdk_app = App(outdir=str(CDK_OUT_DIR))
```

Added `infra_customer/cdk.out/` to `.gitignore`.

### CDK Context Cache (`cdk.context.json`)

CDK creates `cdk.context.json` to cache AWS lookups (like availability zones) so subsequent synths don't need API calls. Example content:

```json
{
  "availability-zones:account=266117665083:region=us-east-1": [
    "us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1e", "us-east-1f"
  ]
}
```

Since we pass explicit AZs rather than using CDK lookups, this file isn't required. Added to `.gitignore` along with `cdk.out/`.

### Result

All 4 CDK stacks now deploy successfully:
- `devopshero-vpc-cdk` — VPC with proper AZs and auto-selected CIDR
- `devopshero-ecs-cluster-cdk` — ECS cluster using the VPC
- `devopshero-ecr-simple-dashboard` — ECR repository
- `devopshero-app-with-alb-simple-dashboard` — ALB, ECS service, ACM cert, Route53

---

## 2026-01-06 - CDK Bootstrap & cdk.out Exploration

Explored the CDK output folder structure and bootstrapped the customer AWS account for CDK deployments.

### cdk.out Folder Structure

Each CDK stack generates two files:

| File | Purpose |
|------|---------|
| `*.template.json` | The CloudFormation template to deploy |
| `*.assets.json` | Manifest of assets (files, Docker images) to publish before deployment |

Plus shared files: `manifest.json` (app manifest), `tree.json` (construct tree), `cdk.out` (version marker).

The `.assets.json` files reference IAM roles created by CDK bootstrap (e.g., `cdk-hnb659fds-file-publishing-role-...`).

### CDK Bootstrap

Ran `npx cdk bootstrap aws://266117665083/us-east-1` to create the **CDKToolkit** CloudFormation stack. This provisions:
- S3 bucket for file assets
- ECR repository for Docker images
- IAM roles (FilePublishing, ImagePublishing, CloudFormationExecution, Deployment, Lookup)
- SSM parameter storing bootstrap version

**Note:** DevOps Hero's existing customer bootstrap (CloudFormation template creating cross-account IAM role) is separate from CDK bootstrap. CDK bootstrap is specifically for CDK's asset publishing pipeline.

### What `cdk deploy` Actually Does

```
1. SYNTH → Runs CDK app, generates *.template.json + *.assets.json to cdk.out/

2. PUBLISH ASSETS (reads *.assets.json)
   → File assets: zip & upload to S3 bootstrap bucket (uses FilePublishingRole)
   → Docker assets: build & push to ECR bootstrap repo (uses ImagePublishingRole)

3. DEPLOY (for each stack, in dependency order from manifest.json)
   → Upload template to S3 (if >51KB)
   → Call CloudFormation CreateStack/UpdateStack
   → Uses DeploymentActionRole to call CF
   → CF uses CloudFormationExecutionRole to create resources
```

### Bootstrap Roles Explained

- **FilePublishingRole** — Upload file assets (Lambda code, etc.) to S3
- **ImagePublishingRole** — Push Docker images to ECR
- **DeploymentActionRole** — Call CloudFormation APIs
- **CloudFormationExecutionRole** — Used by CF to create/modify AWS resources
- **LookupRole** — Read-only queries during synth (e.g., `Vpc.from_lookup()`)

### Attempted: Bypassing CDK Bootstrap

Explored deploying CDK-generated templates directly via boto3 CloudFormation to avoid the bootstrap requirement:

```python
# Instead of: npx cdk deploy
# We tried: cloudformation_utils.deploy_cloudformation_stack(template_path=...)
```

**Pros:** No bootstrap needed, simpler for asset-free stacks.

**Cons:**
- Must manually maintain stack deployment order (CDK reads this from `manifest.json`)
- No asset support (Lambda code, Docker images via `from_asset()`)
- Reinventing what CDK CLI already does well
- 51KB template limit without S3 upload

**Decision:** Reverted to using `npx cdk deploy`. The bootstrap overhead is worth the reliability.

### Future: Customer Onboarding Options

When a customer connects their AWS account, we need CDK bootstrap in their account. Options:

1. **Run `cdk bootstrap` programmatically** — After customer creates our cross-account role, we assume it and run bootstrap via CLI or SDK.

2. **Include bootstrap in customer's CloudFormation** — The bootstrap template is available:
   ```bash
   npx cdk bootstrap --show-template > bootstrap-template.yaml
   ```
   Could merge with or deploy alongside `cf_install_template.json`.

3. **Two-stack customer setup** — Customer clicks "Connect AWS Account" and we deploy:
   - Stack 1: DevOps Hero cross-account role (existing)
   - Stack 2: CDKToolkit bootstrap stack

**TODO:** Decide which approach is cleanest for customers. For now, manually ran bootstrap on test account.

### Project CDK Setup

This project doesn't use `cdk.json`. Instead, CDK is used programmatically:
- Python `aws-cdk-lib` defines stacks in `deploy_app_cdk.py`
- `App().synth()` generates templates to `cdk.out/`
- `npx cdk` fetches the CLI on-demand (not installed as a project dependency)

---

## 2026-01-05 - Deployment Script Refactoring & CDK Alternative

Refactored the deployment codebase for better modularity and added AWS CDK as an alternative to CloudFormation templates.

### New CDK Deployment Option

Created `deploy_app_cdk.py` — a CDK-based equivalent of the CloudFormation deployment. Same infrastructure (VPC, ECS cluster, ECR, ALB, ECS service), but defined in Python using CDK constructs instead of JSON templates.

**Why CDK?** Exploring whether CDK's type safety and IDE support improve maintainability over Jinja2-templated JSON. Both approaches coexist for comparison.

### Unified Entry Point

Created `deploy_app_simple_dashboard.py` as the single entry point for deployments:

```bash
uv run python deploy_app_simple_dashboard.py                     # Deploy with CF (default)
uv run python deploy_app_simple_dashboard.py --engine cdk        # Deploy with CDK
uv run python deploy_app_simple_dashboard.py --image-tag v1.2.3  # Specific tag
uv run python deploy_app_simple_dashboard.py --engine cdk --synth-only  # CDK synth only
```

The entry point handles:
- App configuration (`AppConfig` for simple-dashboard)
- Credential loading from `.env`
- Cross-account role assumption
- Dispatching to CF or CDK engine

### Extracted Utility Modules

Split common functionality into focused modules:

- `appconfig.py` — `AppConfig` dataclass (unified, `cpu`/`memory` as `int`)
- `ecr_utils.py` — `build_and_push_docker_image()`
- `route53_utils.py` — `get_hosted_zone_id()`
- `iam_utils.py` — `load_credentials_from_env()`, `get_assumed_role_session()`
- `cloudformation_utils.py` — `get_stack_output()`, `get_app_urls()`, stack operations

### File Renames

- `deploy_app.py` → `deploy_app_cf.py` (CloudFormation engine)
- `docker_utils.py` → `ecr_utils.py`

### Teardown Support

Added `--teardown` flag to delete all stacks in reverse dependency order:

```bash
uv run python deploy_app_simple_dashboard.py --teardown
```

**Gotcha: ECR repositories must be empty before deletion.** CloudFormation can't delete an ECR repo containing images. CDK has `empty_on_delete=True`, but raw CloudFormation doesn't. Solution: `ecr_utils.delete_all_ecr_images()` empties the repo before stack deletion.

### Code Style Changes

- `load_env()` renamed to `load_credentials_from_env()` and now raises `RuntimeError` instead of `sys.exit(1)`
- `AppConfig.to_template_vars()` converts `int` fields to `str` for CloudFormation compatibility
- Removed `--infra-only` and `--app-only` flags (always deploy everything)

---

## 2026-01-05 - HTTPS & Custom Domain Support (Milestone M5)

Added HTTPS support with custom domains via ACM and Route53.

### Changes

**`AppConfig` dataclass** — Added two new fields:
- `domain_name`: Full domain (e.g., `"simple-dashboard.chsandbox.com"`)
- `hosted_zone_name`: Route53 zone (e.g., `"chsandbox.com"`)

**`cf_app_with_alb.json`** — When domain is configured, creates:
- ACM certificate with DNS validation (auto-validated via Route53)
- HTTPS listener on port 443 with TLS 1.3 policy
- HTTP→HTTPS redirect (301) on port 80
- Route53 A record (alias to ALB)
- Security group rule for port 443

**`deploy_app.py`** — Added `get_hosted_zone_id()` to look up existing Route53 zone at deploy time, passing the zone ID as a CloudFormation parameter.

### Gotcha: Security Group Names

Removed explicit `GroupName` from the security group. CloudFormation can't replace resources with explicit names (name collision during create-before-delete). Let CF generate names like `{StackName}-{LogicalId}-{Random}`.

### Result

App now accessible at `https://simple-dashboard.chsandbox.com` with valid SSL.

---

## 2026-01-03 - ECS Health Checks: Two Different Mechanisms

There are **two separate health check systems** in an ECS/ALB setup:

| Health Check | Who Runs It | On Failure |
|--------------|-------------|------------|
| **Target Group** (ALB) | Load balancer pings HTTP endpoint | Stops routing traffic to task (task keeps running) |
| **Container** (ECS) | ECS agent runs shell command inside container | Kills and replaces the entire task |

**For deployments**, only the Target Group health check matters. ECS considers a task ready for traffic when the ALB marks it healthy. The container health check is optional—useful as a "liveness probe" to catch deadlocked processes, but not involved in deployment rollouts.

**Rolling deployment behavior:** With `MinimumHealthyPercent: 100` and `MaximumPercent: 200`, ECS spins up a new task first, waits for ALB health checks to pass, then drains the old task. This causes 2 tasks to run temporarily—expected behavior for zero-downtime deploys.

---

## 2026-01-03 - Parameterized App Deployment with Jinja2 Templates

Refactored the deployment script to support deploying any app, not just `simple-dashboard`.


### Hybrid Templating Strategy

The key distinction is **when** values get resolved:

**Jinja2 (render time)** — values baked into JSON before CloudFormation sees it:
- `app_name` in resource names and export names (CF can't parameterize these)
- `environment_variables` as a proper JSON array (CF can't loop)
- Conditional sections like health checks
- Anything structural that doesn't change between deployments of the same app

**CloudFormation Parameters (deploy time)** — resolved by CloudFormation:
- `ImageTag` — changes frequently, visible in AWS Console, can redeploy same template with new value
- Simple string/number substitutions where you want AWS Console visibility

Example: `ImageTag` is a CF Parameter because you deploy the same app repeatedly with different tags. You want to see "what tag is deployed?" in the Console, and CF can detect "no changes needed" if you redeploy with the same tag.


### Code Organization

Split `infra_customer/` into focused modules:

```
infra_customer/
├── deploy_app.py           # Main script + AppConfig dataclass
├── vpc_utils.py            # CIDR overlap detection, available range finder
├── ecs_service_stable.py   # Service stabilization with failure diagnostics
├── cf_ecr.json             # Jinja2: ECR repository
└── cf_app_with_alb.json    # Jinja2: Task Definition + ALB + ECS Service
```

### AppConfig Dataclass

All app-specific settings in one place, passed down through functions:

```python
AppConfig(
    app_name="simple-dashboard",
    ecr_repo_name="devopshero/simple-dashboard",
    container_port=8501,
    health_check_path="/_stcore/health",
    health_check_command="...",
    environment_variables=[...],
    app_source_path=Path(...),
)
```

### Simplifications

- Removed no-ALB deployment path (all apps get ALB)
- Removed default function parameters per project style guide

---

## 2025-12-29 - Decoupled ECS Cluster Stack from VPC Stack

Moved the default security group from `cf_ecs_cluster.json` to `cf_vpc.json`. The ECS cluster stack now has zero VPC dependencies, avoiding CloudFormation's "export in use" lock when updating the VPC stack.

## 2025-12-29 - VPC Architecture Change: NAT Gateway for ECS Tasks

Changed the customer VPC from public-subnet-with-public-IP to private-subnet-behind-NAT-Gateway.

**Before:** Fargate tasks ran in public subnets and acquired public IPs to reach ECR/internet.  
**After:** Fargate tasks run in private subnets; outbound traffic goes through a NAT Gateway.

**Rationale:** Cleaner security posture—tasks have no public IPs. The ~$32/month NAT cost is acceptable.

**Future:** Plan to support multiple networking models based on customer preference (e.g., NAT Gateway, public IP, VPC endpoints only).

---

## 2025-12-29 - Customer Account Infrastructure Templates (VPC + ECS Cluster)

### Summary

Created CloudFormation templates and a Python deployment script to initialize customer AWS accounts with the infrastructure needed to run Fargate apps. Successfully deployed to test account `266117665083`.

### Files Created

```
infra_customer/
├── cf_vpc.json              # VPC with 2 public subnets
├── cf_ecs_cluster.json      # ECS cluster, security group, IAM roles
└── test_deploy_infra.py     # Python script to deploy via cross-account role
```

### Architecture Decision: Private Apps with Public Subnets

Apps are private (accessible only from VPC via VPN), but Fargate tasks run in **public subnets with public IPs**. This avoids NAT Gateway costs (~$32/month) while still allowing tasks to pull images from ECR.

```
┌─────────────────────────────────────────┐
│              VPC (172.20.0.0/20)        │
│  ┌───────────────────────────────────┐  │
│  │  Public Subnet 1 (172.20.0.0/24)  │  │
│  │  Public Subnet 2 (172.20.1.0/24)  │  │
│  │  └── Fargate Tasks (public IP)    │◄── VPN access only
│  └───────────────────────────────────┘  │
│  └── Internet Gateway                   │
└─────────────────────────────────────────┘
```

Security group restricts inbound to VPC CIDR only.

### CIDR Range Selection: 172.x.x.x with Auto-Conflict Avoidance

**Why 172.16-31.x.x instead of 10.x.x.x or 192.168.x.x:**
- `10.x.x.x` — Most commonly used by enterprises, higher conflict risk
- `192.168.x.x` — Used by home networks, causes VPN routing issues for developers
- `172.16-31.x.x` — Rarely used, VPN-friendly, good middle ground

**Automatic CIDR selection:** The Python script scans existing VPCs in the customer account and picks the first available `/20` block in `172.20-31.x.x` that doesn't overlap.

```python
def find_available_vpc_cidr(ec2_client) -> dict:
    # Gets all existing VPC CIDRs
    # Tries 172.20.0.0/20, 172.20.16.0/20, etc.
    # Returns first non-conflicting CIDR with subnet allocations
```

### CloudFormation Exports & Cross-Stack Dependencies

The VPC stack exports values that the ECS cluster stack imports:
- `devopshero-vpc-id`
- `devopshero-vpc-cidr`
- `devopshero-public-subnet-1`, `devopshero-public-subnet-2`

**Lesson learned:** CloudFormation prevents modifying exported values if another stack imports them. When we tried to change the VPC CIDR after ECS cluster was deployed:

```
Cannot update export devopshero-vpc-cidr as it is in use by devopshero-ecs-cluster
```

**Solution:** Delete stacks in reverse dependency order, then recreate. This is fine because VPC CIDRs are effectively immutable anyway.

**Considered nested stacks** but decided against for now — adds complexity (S3 hosting, harder debugging) for minimal benefit with just 2 stacks.

### Cross-Account Deployment via AssumeRole

The Python script:
1. Loads DOH control plane credentials from `.env`
2. Assumes the `devopshero-{external_id}` role in the target account
3. Deploys CloudFormation stacks with the assumed credentials

```python
session = get_assumed_role_session(
    access_key=os.getenv("DOH_AWS_ACCESS_KEY"),
    secret_key=os.getenv("DOH_AWS_SECRET_KEY"),
    account_id="266117665083",
    external_id="9e62c988-09dd-4f96-b5a7-a67646dd285b",
    region="us-east-1",
)
```

### What Got Deployed

| Stack | Resources |
|-------|-----------|
| `devopshero-vpc` | VPC, 2 public subnets, Internet Gateway, route table |
| `devopshero-ecs-cluster` | ECS cluster, security group, Task Execution Role, Task Role, CloudWatch log group |

### Next Steps

1. Create per-app CloudFormation template (ECR repo + Task Definition + ECS Service)
2. Add Docker build & push to ECR in the Python script
3. Deploy `simple_dashboard` end-to-end
4. Get a working URL accessible via VPN

---

## 2025-12-28 - Created Simple Dashboard (Track 1 MVP App)

### Summary

Created `deployable_repos/simple_dashboard/` — a minimal Streamlit app to test the deployment pipeline.

### Files Created

```
simple_dashboard/
├── app.py                      # Streamlit dashboard with fake metrics
├── pyproject.toml              # For local dev with uv
├── requirements.txt            # For Dockerfile
├── Dockerfile                  # Uses uv for fast installs
├── README.md
└── .streamlit/
    ├── credentials.toml        # Skips email prompt
    └── config.toml             # Disables telemetry
```

### Streamlit First-Run Prompt Skip

Streamlit shows an email collection prompt on first run. To skip it, create `.streamlit/credentials.toml`:

```toml
[general]
email = ""
```

And `.streamlit/config.toml` to disable telemetry:

```toml
[browser]
gatherUsageStats = false

[server]
headless = true
```

### Local Development

```bash
cd deployable_repos/simple_dashboard
uv run streamlit run app.py
```

`uv run` automatically creates an isolated `.venv`, installs deps from `pyproject.toml`, and runs the app.

---

## 2025-12-28 - Strategic Direction: Dual-Track MVP Approach

### Summary

Defined the strategic approach for finding product market fit: build a simple deployment pipeline first, then incrementally add features toward deploying complex enterprise apps.

### The Problem

We had built solid infrastructure (auth, org management, AWS account connection) but zero core product functionality. The gap between "connected AWS account" and "deployed app" was undefined.

### The Decision: Dual-Track Approach

Rather than attempting to deploy a complex app immediately, we'll pursue two parallel tracks:

**Track 1: Simple Dashboard → Working Deployment**
- Create a minimal Streamlit app with no dependencies
- Build the core deployment pipeline: Build → ECR → Fargate → URL
- Prove the loop closes end-to-end
- Target: days, not weeks

**Track 2: Feature Roadmap → db_portal**
- Use `db_portal` (existing Phoenix/Elixir internal tool) as the north star
- Each milestone adds one capability that enterprise apps need
- Eventually deploy db_portal as proof of enterprise readiness

### Feature Milestones (Track 2)

| Milestone    | Feature                    | db_portal Requirement                            |
|--------------|----------------------------|--------------------------------------------------|
| **M1**       | Basic deploy (Streamlit)   | N/A (foundation)                                 |
| **M2**       | Environment variables      | `RUN_SAMPLER`, `SSLCERT_MODE`, `LE_MODE`         |
| **M3**       | Secrets injection          | `secret_key_base`, `signing_salt`, `db_password` |
| **M4**       | Managed RDS/Aurora         | MySQL database dependency                        |
| **M5**       | Custom domain + TLS        | `dataengr.humanityrules.io` with certs              |
| **M6**       | SSO integration            | Okta SAML                                        |
| **M7**       | Private VPC networking     | Aurora connectivity, no public internet          |
| **M8**       | Background workers         | Sampler scheduler process                        |

### Why This Approach

1. **Faster learning** — Get a working deployment in days, not weeks
2. **Avoid scope creep** — Don't get lost in db_portal-specific issues
3. **Incremental value** — Each milestone is independently demoable
4. **Clear north star** — db_portal keeps us honest about enterprise requirements

### Target Persona

Data Scientists / ML Engineers who can build apps but struggle with deployment. They represent:
- Maximum pain (deployment is mystical to them)
- Growing market (vibe coding trend)
- Simpler initial scope (stateless web UIs)
- Clear success metric ("I have a URL")

### Folder Structure

```
deployable_repos/
├── db_portal/           # Track 2 goal (complex Phoenix app)
└── simple_dashboard/    # Track 1 MVP (minimal Streamlit app)
```

### Next Steps

1. Create `simple_dashboard/` with Streamlit app + Dockerfile
2. Manually deploy to Fargate to understand the AWS plumbing
3. Automate the pipeline in DevOps Hero
4. Wire to UI: App model + "Deploy" button + status page

---

## 2025-12-28 - Fixed Dropdown Popover Width Issue

### Summary

Fixed a visual bug where the organization dropdown's options list was rendering full-width instead of matching the button width.

### The Problem

The `el-options` popover element was using `w-(--button-width)` to match the button width, but the `--button-width` CSS variable was never being set by the Tailwind Plus Elements library. Since popover elements render in the browser's "top layer" (outside normal document flow), they don't inherit width from parent containers.

### The Fix

Added CSS Anchor Positioning rules in `styles.css`:

```css
el-select {
  anchor-name: --select-anchor;
}

el-options[popover] {
  position-anchor: --select-anchor;
  width: 15rem;                /* fallback for older browsers */
  width: anchor-size(width);   /* uses anchor's width in modern browsers */
}
```

Also removed the broken `w-(--button-width)` class from `_dropdown_select.html` since the width is now handled via CSS.

### Why This Approach

- **CSS Anchor Positioning** is the modern way to link a popover's dimensions to its anchor element
- The **fallback width** (`15rem`) ensures reasonable behavior in browsers without full anchor positioning support
- By moving this to CSS rather than relying on the Tailwind Plus Elements library to set a CSS variable, we have direct control over the behavior

---

## 2025-12-27 (evening) - Real User/Org Context & Organization Switcher

### Summary

Replaced all hardcoded fake data in the app shell with real user and organization data from the database. Added working organization switcher.

### Changes

- **`get_app_shell_context()`**: Now takes `request` param and pulls real data (user name, email, initials, organizations)
- **Organization switcher**: Dropdown in sidebar now actually switches organizations via `/switch-organization/` endpoint
- **`current_organization` on User model**: Moved from session storage to a FK on User. Made it NOT NULL with `on_delete=PROTECT`
- **Removed avatar**: Replaced profile image with user initials in colored circle
- **Removed `get_current_organization()`**: Was just `return request.user.current_organization`

### Migrations

- `0005_add_current_organization_to_user.py`
- `0006_make_current_organization_required.py` (data migration + NOT NULL)

---

## 2025-12-27 - Backend Callback Endpoint & Infrastructure Refinements

### Summary

Completed the AWS account connection flow by implementing the backend API endpoint that receives callbacks from the Lambda. Also standardized naming conventions and improved Lambda logging.

### What We Built

#### Backend Callback Endpoint (`/api/aws/install-account-callback`)

Created `devopshero_app/views/api.py` with the endpoint that:
- Validates Bearer token authentication
- Validates `external_id` is a proper UUID (prevents Django 500 errors)
- Finds the `AWSAccount` record by `external_id`
- Updates status to `CONNECTED` on Create, handles Update/Delete appropriately
- Returns clean JSON responses for all error cases

#### Lambda Logging Fix

Replaced `print()` statements with Python's `logging` module. `print()` in Lambda can have buffering issues and doesn't reliably appear in CloudWatch. The logging module is the recommended approach.

### ngrok for Local Testing

Enabled ngrok tunneling (`https://devopshero.ngrok.io`) so the Lambda can call our local Django server:
- Added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- Made OAuth redirect URI dynamic (builds from request host)

### Issues Encountered

- **UUID validation**: Passing invalid UUIDs to the endpoint caused Django 500 errors. Added explicit UUID validation before the database query.
- **WorkOS trailing slash**: WorkOS dashboard rejects redirect URIs with trailing slashes.

---

## 2025-12-26 - AWS Infrastructure Setup for Cross-Account Access

### Summary

Today we built the AWS infrastructure that allows DevOpsHero to connect to customer AWS accounts. The system uses CloudFormation to create IAM roles in customer accounts, with a callback mechanism to automatically notify DevOpsHero when a customer completes the setup.

### What We Built

#### 1. Install Callback Lambda (`cf_install_callback_lambda.json` + `install_callback_lambda.py`)

We created a Lambda function that acts as a CloudFormation Custom Resource handler. When a customer deploys our CloudFormation template in their AWS account, this Lambda is automatically invoked to notify the DevOpsHero backend.

**Why:** Without this callback, customers would have to manually provide their AWS Account ID after deploying the stack, and we'd have no confirmation the deployment actually succeeded. The callback automates this—CloudFormation itself tells us the deployment completed and provides the account ID and Role ARN directly.

#### 2. S3 Buckets (`cf_public_bucket.json` + `cf_private_bucket.json`)

We created two S3 buckets:

- **devopshero-public**: Hosts the customer-facing CloudFormation template (`cf_install_template.json`). Must be public so AWS Console can fetch it via the quick-create URL.
- **devopshero-private**: Stores the Lambda code zip file. Private because it contains internal implementation details.

Both buckets have versioning enabled for rollback capability.

#### 3. Customer Install Template (`cf_install_template.json`)

The CloudFormation template that customers deploy in their AWS accounts. It creates:
- An IAM role with `AdministratorAccess` that DevOpsHero can assume
- A custom resource that calls our callback Lambda

**Security:** Uses an `ExternalId` parameter to prevent confused deputy attacks. Each customer gets a unique ExternalId stored in our database.

#### 4. Deployment Scripts

- `run_devops_deployment.sh`: Master script that deploys all infrastructure in the correct order
- `upload_s3_files.sh`: Uploads Lambda code and install template to S3
- `update_install_callback_lambda.sh`: Quick script to update just the Lambda code

### Technical Decisions

**Why separate the Lambda code into a .py file?**
Originally the Python code was embedded in the CloudFormation template using `ZipFile`. Extracting it to `install_callback_lambda.py` makes the code easier to read, edit, and test. The tradeoff is we now need S3 to host the zip file.

**Why two buckets instead of one?**
Security principle of least privilege. The public bucket only contains the install template (which customers need to see anyway). The Lambda code stays private.

**Chicken-and-egg problem:**
The Lambda needs its code in S3, but S3 must exist first. We solved this by ordering the deployment script:
1. Create buckets
2. Upload files to S3
3. Deploy Lambda

### Issues We Encountered

1. **Invalid RetentionInDays**: CloudWatch Logs only accepts specific values (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, etc.). We tried 768, had to change to 731.

2. **ROLLBACK_COMPLETE state**: When a CloudFormation stack fails during creation, it enters this state and cannot be updated—only deleted. Added delete-and-wait logic to the deployment script.


### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Customer's AWS Account                       │
│                                                                 │
│  CloudFormation Stack                                           │
│  ├── IAM Role (devopshero-{external_id})                        │
│  │   └── Allows DevOpsHero account to AssumeRole                │
│  └── Custom Resource ──────────────────────────────────────┐    │
│                                                            │    │
└────────────────────────────────────────────────────────────│────┘
                                                             │
                                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   DevOpsHero AWS Account (555553041615)         │
│                                                                 │
│  ┌─────────────────┐    ┌──────────────────────────────────┐    │
│  │ S3 (public)     │    │ Lambda: devopshero-install-callback│  │
│  │ - install tpl   │    │                                    │  │
│  └─────────────────┘    │ Receives: AccountId, RoleArn,      │  │
│                         │           ExternalId, Region       │  │
│  ┌─────────────────┐    │                                    │  │
│  │ S3 (private)    │    │ Calls: DevOpsHero Backend API      │  │
│  │ - lambda code   │    └───────────────────────────────────┘   │
│  └─────────────────┘                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### TODO

- ~~Implement the backend API endpoint `/api/aws/account-callback` to receive Lambda callbacks~~ ✅ Done (2025-12-27)
- Add error handling in the callback Lambda for network failures (retries?)
- Consider adding SNS notifications for failed stack deployments
- Test the full flow end-to-end with a real CloudFormation deployment
- Add CloudWatch alarms for Lambda errors (WE ARE MISSING CUSTOMERS!!!!)
- Document the customer onboarding flow
- Reduce IAM permissions from AdministratorAccess to least-privilege (later, once we know exactly what's needed)
- Rearchitecture DOH infra stack to use nested stacks, while solving the chicken and egg problem between S3 and 
  lambda code by keeping the private bucket in its own independent stack.
