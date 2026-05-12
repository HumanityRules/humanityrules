# End-to-End Verification

Changes to the Hermes agent often require running the full deploy → container-boot → agent-runtime loop to verify. Unit tests alone don't live deployment problems with, for example, the Dockerfile, entrypoint, supervisor, skill-pruning, patch-application, or integration-broker regressions.

Sometimes the developer will want you doing it himself, other times he will prefer that you do it. If you are making a change like this, **ask** the developer if he wants end-to-end verification.

## How we verify

**Management commands:** are the primary mechanism for end-to-end verification. They drive the same code paths the UI does (deploy, redeploy, update live app, run jobs, etc.) without needing browser interaction. See the `manage-commands` skill.
**Job worker:** `run_job_worker` is often running in the background. Deployments, redeploys, and most async work go through it, so **if your change touches code the worker imports you must stop and restart it** — otherwise the worker keeps executing the old code and your verification is meaningless.
**AppTemplate reseeding:**If you update the AppTemplate, don't forget to call seed_app_templates so that they are updated in the database.

## Test environment

- **AWS Account:** `Humanity Rules Sandbox`.
- **Environment:** `default`.
- **Blast radius:** this is a throwaway test environment. Destroy, redeploy, or mutate anything you need — no fear.

## What to reuse vs. redeploy

- **Existing app (e.g. `hermes-vmendi00`):** if one is already running, reuse it. Two cheap options:
    - **Update the live app** — fastest path for iterating on agent-runtime code (skills, webui, supervisor, config templates) that gets re-rendered/re-pushed without a full redeploy.
    - **Redeploy it** — use when the change requires a fresh container image, new CloudFormation resources, or anything picked up only at deploy time (Dockerfile, entrypoint, infra).
- **New app from template:** when the change is in the deployment pipeline itself (blueprint generation, template resolution, CDK/CFN stacks, app bootstrap) and needs a clean slate. Deploy a new app from the Hermes template in `Humanity Rules Sandbox` / `default`.
