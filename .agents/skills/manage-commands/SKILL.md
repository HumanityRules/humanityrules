---
name: manage-commands
description: Understand and use available HUMR's management commands, used to manage the plaform, and to troubleshoot, debug, or run tests. If you are trying to diagnose, debug, or verify your work, you probably need this skill.
---

# Django Management Commands

All custom commands live in `humanityrules_app/management/commands/`.

**To get detailed usage for any command**, read the first 30 lines of its file — every command has a module-level docstring with usage examples and instructions at the top.


## Command Reference

- **humr_query** — Ad-hoc model queries (fields, filters, ordering). Preferred over `shell -c` for plain reads (formatted table, JSON output, no Python).
- **humr_control** — Control plane ops: provision/teardown environments; deploy from AppTemplate (`deploy-app-template`), redeploy, and remove apps (`remove-app --app <slug>`, which tears down infra and purges secrets/persistent-data/policies).
- **humr_efs_browse** — Browse EFS filesystem via ECS Exec (interactive shell in customer environment).
- **humr_app_logs** — Fetch CloudWatch logs for a customer app (works for running and crashed/stopped tasks), by account + env + app slug.
- **humr_app_shell** — Interactive shell in a deployed customer app *container* (ECS Exec / SSM). For humans.
- **humr_app_exec** — Non-interactive script execution in a customer app *container*. Script on stdin or `--script-file`; supports `--as USER`, `--timeout`, `--cwd`, `--set KEY=VALUE`, `--format json`. Prefer over `humr_app_shell --command` for scripted probes.
- **humr_node_shell** — Interactive shell on the customer EC2 *host* (container instance) via SSM Session Manager. Use for host-level inspection — kernel, Docker/containerd, host bind-mount dirs (e.g. `/var/lib/humr/hermes-roots/`), disk space. Not for container internals (use `humr_app_shell`). Supports `--list` (inventory of instances + tasks), `--app <slug>` (auto-pick the instance running an app), `--instance-id <id>`.
- **humr_hermes_migrate** — Migrate a `hermes_agent` app's persistent-root state from one HUMR env to another (same- or cross-account). Five phases (`upload,stage,host-clear,finalize,verify`) with `cleanup` opt-in. See the `hermes-migrate` skill for the full workflow.
- **humr_secrets** — Manage customer Secrets Manager secrets: list, purge, and shared environment secrets (shared-list/shared-set/shared-delete). All subcommands take --account and optional --org (name or slug).
- **humr_reset_org_abac** — Full factory reset of ABAC Policy rows for one organization (`--org` slug or name; optional `--admin-email`). Deletes all org policies, re-runs seed bootstrap, recreates default per-app open-access policies.
- **humr_bootstrap_sandbox** — Idempotently bootstrap the shared HUMR sandbox: backfills a connected sandbox account + ready env per org, and verifies the shared base infra (VPC/cluster/ALB/EFS) exists. Base infra is verify-only by default (`--provision-base` to actually create it). Safe to run on every deploy.
- **run_job_worker** — Background job worker for deployments and environment provisioning.
- **seed_app_templates** — Seed `AppTemplate` records (the deployable app catalog, e.g. the OpenClaw AI assistant template).
- **seed_test_apps** — Seed orgs, workspaces, environments, and apps for UI testing.
- **seed_test_groups** — Seed test groups with attributes for ABAC testing.
- **seed_test_users** — Seed test users with group memberships.
- **seed_local_app** — Seed DB rows for a local Hermes compose app (App stub, localhost Environment, the App's bearer token); prints the `HUMR_APP_BEARER=...` block for `hermes_agent_local/.env`.
- **setup_oidc_org** — Create/update an organization with OIDC (Okta) auth config.
- **setup_google_oauth_client** — Load a Google OAuth 2.0 Client ID JSON (from Cloud Console) into `IntegrationConfig`; re-running rotates it in place.
- **setup_x_oauth_client** — Store X (Twitter) OAuth 2.0 client id/secret/redirect-uris into `IntegrationConfig` via flags (X gives no downloadable JSON); re-running rotates it in place.
- **ensure_superuser** — Create or promote a user to superuser.


## Local execution (default — use unless user explicitly says "prod")

Do not ask the user "local or prod?" — just run locally. They'll say "prod" when they mean prod.


```bash
uv run manage.py <command> [args...]
```

## Production execution (only when user explicitly says "prod" / "production")

Via `infra_humanityrules/prod_manage.sh` (runs on the ECS container). See the `prod-manage` skill for setup.

```bash
cd infra_humanityrules
./prod_manage.sh <command> [args...]
# e.g. ./prod_manage.sh humr_query Deployment status --filter status=failed --limit 10
```