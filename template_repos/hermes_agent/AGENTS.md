# End-to-End Verification

Changes to the Hermes agent often require running the full deploy → container-boot → agent-runtime loop to verify. Unit tests alone don't live deployment problems with, for example, the Dockerfile, entrypoint, supervisor, skill-pruning, patch-application, or integration-broker regressions.

If you are making a change that requires verification, **ask** the developer if he wants you to run end-to-end verification.

When you need an instance of a Hermes agent to run your verification, deploy it anew from AppTemplate by using the management command `doh_control deploy-app-template`. **Always pass `--label <unique-tag>`** so your verification is scoped to its own job worker (see below).

## How we verify

**Management commands** are the primary mechanism for end-to-end verification. They drive the same code paths the UI does (deploy, redeploy, update live app, run jobs, etc.) without needing browser interaction. See the `manage-commands` skill.

**Job worker scoping (critical for parallel work):** Deployments, redeploys, and most async work go through `run_job_worker`. To avoid colliding with the unscoped main worker (or with other Claudes working in parallel worktrees), **never stop other workers** — instead, run your *own* labelled worker:

1. Pick a unique label for your verification run (e.g. `vmendi-A`, your worktree branch name, etc.).
2. In your worktree, start a labelled worker: `uv run manage.py run_job_worker --label <your-label>`. This worker only claims work for Apps stamped with that exact label.
3. Pass the *same* label to `deploy-app-template`: `doh_control deploy-app-template ... --label <your-label>`. This stamps the new App's `label` so only your worker picks up its deploy/redeploy/permission-apply/teardown/removal jobs.
4. Other `doh_control` subcommands (`redeploy-app`, `teardown-app`, etc.) need no `--label`; they read it transitively from the App.

The unscoped main worker (no `--label`) keeps running and serves UI traffic + env-level jobs. Leave it alone.

**Code reload:** if your change touches code the worker imports, stop and restart *your labelled worker* so it picks up the new code. The main worker on the host can keep running stale code — it never sees your labelled work.

**AppTemplate reseeding:** If you update the AppTemplate, don't forget to call seed_app_templates so that they are updated in the database.

## Test environment

- **AWS Account:** `Humanity Rules Sandbox`.
- **Environment:** `default`.
- **Blast radius:** this is a throwaway test environment. Destroy, redeploy, or mutate anything you need — no fear.