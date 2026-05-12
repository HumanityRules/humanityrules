# End-to-End Verification

Changes to the Hermes agent often require running the full deploy → container-boot → agent-runtime loop to verify. Unit tests alone don't live deployment problems with, for example, the Dockerfile, entrypoint, supervisor, skill-pruning, patch-application, or integration-broker regressions.

If you are making a change that requires verification, **ask** the developer if he wants you to run end-to-end verification. Sometimes the developer will want you doing it himself, other times he will prefer that you do it. 

When you need an instance of a Hermes agent to run your verification, deploy it anew from AppTemplate by using the management command "doh_control dpeloy-app-template"

## How we verify

**Management commands** are the primary mechanism for end-to-end verification. They drive the same code paths the UI does (deploy, redeploy, update live app, run jobs, etc.) without needing browser interaction. See the `manage-commands` skill.

**Job worker:** `run_job_worker` is often running in the background. Deployments, redeploys, and most async work go through it, so **if your change touches code the worker imports you must stop and restart it** — otherwise the worker keeps executing the old code and your verification is meaningless. If an existing `run_job_worker` is already running (e.g. from the main tree or another worktree), stop it without asking the user and start a fresh one from your worktree for the deploy.

**AppTemplate reseeding:** If you update the AppTemplate, don't forget to call seed_app_templates so that they are updated in the database.

## Test environment

- **AWS Account:** `CH Sandbox`.
- **Environment:** `default`.
- **Blast radius:** this is a throwaway test environment. Destroy, redeploy, or mutate anything you need — no fear.