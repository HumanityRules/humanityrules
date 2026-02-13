# Analysis: Missing Stack Concept in DOH's Domain Model

> Prompt: In Pulumi, the Stack is a single instance of the cloud infrastructure that you define, and typically you'll have different stacks for your different environments like staging, prod, or dev. Is there such an idea in our domain model? If the answer is no, what are the consequences of such an omission?

---

## What Pulumi Stack Means

A Pulumi Stack binds together: infrastructure definition + configuration values + target environment. You define your infrastructure once, and each Stack instance (dev, staging, prod) carries its own config — different CPU, different env vars, different secrets, different scaling.

## What DOH Has Instead

In DOH's model, the **App** is both the definition and the configuration, and there is exactly one configuration per App:

- **App** stores: cpu, memory, container_port, health_check_path, environment_variables, app_secrets, dockerfile_path, branch
- **Deployment** stores: git_ref, image_tag, subdomain, status — but **not** the configuration used to deploy
- **Environment** is just the infrastructure target (VPC + ECS cluster) — it carries zero app-specific configuration

When the agent calls `deploy_app`, it **overwrites the App record's fields in-place** (`deploy_app.py:450-465`), then creates a Deployment that references the App. When the job worker executes, `build_app_config()` reads from the current App model state (`app_config_builder.py:60-97`). Nothing is snapshotted.

## Consequences

**1. No per-environment configuration differentiation.** If "my-app" is deployed to both staging and prod, both environments get the same cpu, memory, env vars, and secrets. You can't have staging at 256 CPU with a staging DATABASE_URL and prod at 2048 CPU with a prod DATABASE_URL. The only workaround today is creating two separate Apps ("my-app-staging" and "my-app-prod"), which breaks the conceptual link between them.

**2. Sequential deploys to different environments corrupt each other.** If the agent deploys to staging with cpu=256, then deploys to prod with cpu=2048, the App record now reads cpu=2048. A subsequent redeploy to staging would use 2048 — the prod config — because the App was mutated by the prod deployment.

**3. Deployments are not reproducible.** Since the Deployment doesn't snapshot the configuration, there's no way to know what cpu/memory/env vars were active when a past deployment ran. You can't "redeploy deployment #3 with its original settings."

**4. No promotion workflow.** In Pulumi you promote staging to prod by deploying the same code artifact with the prod Stack's config. In DOH there's no way to express "deploy this git ref to prod, but with prod-specific settings" without mutating the shared App record.

**5. Subdomain is the sole exception.** The `subdomain` field lives on Deployment (not App), so it does vary per environment. Every other operational parameter lives on App only.

## The Missing Entity

The missing entity is something like a **per-(App, Environment) configuration record** — the thing that would hold "my-app's settings when deployed to staging" separately from "my-app's settings when deployed to prod." That's what a Pulumi Stack provides.
