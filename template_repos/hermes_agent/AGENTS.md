# Vendored Hermes forks (`vendor/`)

The Hermes agent and WebUI are vendored as **git submodules** under `vendor/hermes-agent` and `vendor/hermes-webui`, pinned to our `humr/v*` fork branches in the private `HumanityRules/hermes-{agent,webui}` mirrors. HUMR changes to Hermes are **commits on those fork branches**. The Dockerfile copies the agent verbatim and overlays the webui fork onto the base image's `/apptoo` (see `build/overlay-webui.sh`).

**Materialize before building.** A fresh checkout leaves the submodules empty; an empty submodule COPYs into the image as an empty dir with no error. Run once:

    git submodule update --init template_repos/hermes_agent/vendor/hermes-agent
    git submodule update --init template_repos/hermes_agent/vendor/hermes-webui

`build/check-vendor.sh` is a build-time fail-fast guard for this, and also asserts the webui fork was rebased onto the same upstream version as the base image (`FROM ...hermes-webui:<WEBUI_BASE_VERSION>` vs `.humr-upstream-version`).

**To change the agent or webui:** edit the files under `vendor/<repo>` (the edit is picked up by local builds immediately, uncommitted). To record it, use `build/vendor-commit.sh <agent|webui> -m "msg"` — it commits on the `humr/v*` branch, pushes to the mirror, *then* bumps the superproject gitlink, in that order. Do not hand-roll this: committing a gitlink that points at an unpushed commit produces a SHA that fresh clones can't fetch.

**Gitlink hygiene.** After testing inside a submodule, `git status` in the superproject shows `modified: vendor/... (new commits)` or `(modified content)`. A blind `git add -A` on `main` would ship an unintended pin bump or an uncommitted vendor change. Only stage `vendor/<repo>` via `vendor-commit.sh`, or deliberately after the fork commit is pushed.

**Version bump:** rebase the `humr/v*` branch onto the new upstream tag, update `.humr-upstream-version` (webui), push, then `git -C vendor/<repo> checkout humr/vNEW` and commit the gitlink. See the `hermes-update-check` skill.

# End-to-End Verification

Changes to the Hermes agent often require running the full deploy → container-boot → agent-runtime loop to verify. Unit tests alone don't live deployment problems with, for example, the Dockerfile, entrypoint, supervisor, skill-pruning, vendored-fork overlay, or integration-broker regressions.

If you are making a change that requires verification, **ask** the developer if he wants you to run end-to-end verification.

When you need an instance of a Hermes agent to run your verification, deploy it anew from AppTemplate by using the management command `humr_control deploy-app-template`. **Always pass `--label <unique-tag>`** so your verification is scoped to its own job worker (see below).

## How we verify

**Management commands** are the primary mechanism for end-to-end verification. They drive the same code paths the UI does (deploy, redeploy, update live app, run jobs, etc.) without needing browser interaction. See the `manage-commands` skill.

**Job worker scoping (critical for parallel work):** Deployments, redeploys, and most async work go through `run_job_worker`. To avoid colliding with the unscoped main worker (or with other Claudes working in parallel worktrees), **never stop other workers** — instead, run your *own* labelled worker:

1. Pick a unique label for your verification run (e.g. `vmendi-A`, your worktree branch name, etc.).
2. In your worktree, start a labelled worker: `uv run manage.py run_job_worker --label <your-label>`. This worker only claims work for Apps stamped with that exact label.
3. Pass the *same* label to `deploy-app-template`: `humr_control deploy-app-template ... --label <your-label>`. This stamps the new App's `label` so only your worker picks up its deploy/redeploy/permission-apply/teardown/removal jobs.
4. Other `humr_control` subcommands (`redeploy-app`, `teardown-app`, etc.) need no `--label`; they read it transitively from the App.

The unscoped main worker (no `--label`) keeps running and serves UI traffic + env-level jobs. Leave it alone.

**Code reload:** if your change touches code the worker imports, stop and restart *your labelled worker* so it picks up the new code. The main worker on the host can keep running stale code — it never sees your labelled work.

**AppTemplate reseeding:** If you update the AppTemplate, don't forget to call seed_app_templates so that they are updated in the database.

**AWS signer service list:** When adding or removing a supported AWS service in `humr_runtime/aws_signer.py` or the child AWS config in `humr_runtime/sandbox_seed.py`, update `skills/development/aws-cli/SKILL.md`'s signer services list in the same change.

## Test environment

- **AWS Account:** `CH Sandbox`.
- **Environment:** `default`.
- **Blast radius:** this is a throwaway test environment. Destroy, redeploy, or mutate anything you need — no fear.
