---
name: manage-commands
description: Understand and use available DOH's management commands, used to manage the plaform, and to troubleshoot, debug, or run tests. If you are trying to diagnose, debug, or verify your work, you probably need this skill.
---

# Django Management Commands

All custom commands live in `devopshero_app/management/commands/`.

**To get detailed usage for any command**, read the first 30 lines of its file — every command has a module-level docstring with usage examples and instructions at the top.


## Command Reference

- **doh_query** — Ad-hoc model queries (fields, filters, ordering). Preferred over `shell -c` for plain reads (formatted table, JSON output, no Python).
- **doh_control** — Control plane ops: provision/teardown environments; deploy from AppTemplate (`deploy-app-template`), redeploy, teardown, and remove apps (`teardown-app --remove-app` for full cleanup including secrets/persistent-data/policies).
- **doh_efs_browse** — Browse EFS filesystem via ECS Exec (interactive shell in customer environment).
- **doh_app_logs** — Fetch CloudWatch logs for a customer app (works for running and crashed/stopped tasks), by account + env + app slug.
- **doh_app_shell** — Interactive shell in a deployed customer app *container* (ECS Exec / SSM). For humans.
- **doh_app_exec** — Non-interactive script execution in a customer app *container*. Script on stdin or `--script-file`; supports `--as USER`, `--timeout`, `--cwd`, `--set KEY=VALUE`, `--format json`. Prefer over `doh_app_shell --command` for scripted probes.
- **doh_node_shell** — Interactive shell on the customer EC2 *host* (container instance) via SSM Session Manager. Use for host-level inspection — kernel, Docker/containerd, host bind-mount dirs (e.g. `/var/lib/devopshero/hermes-roots/`), disk space. Not for container internals (use `doh_app_shell`). Supports `--list` (inventory of instances + tasks), `--app <slug>` (auto-pick the instance running an app), `--instance-id <id>`.
- **doh_secrets** — Manage customer Secrets Manager secrets: list, purge, and shared environment secrets (shared-list/shared-set/shared-delete). All subcommands take --account and optional --org (name or slug).
- **doh_reset_org_abac** — Full factory reset of ABAC Policy rows for one organization (`--org` slug or name; optional `--admin-email`). Deletes all org policies, re-runs seed bootstrap, recreates default per-app open-access policies.
- **run_job_worker** — Background job worker for deployments and environment provisioning.
- **seed_test_apps** — Seed orgs, workspaces, environments, and apps for UI testing.
- **seed_test_groups** — Seed test groups with attributes for ABAC testing.
- **seed_test_users** — Seed test users with group memberships.
- **seed_local_repos** — Scan local directory and create Repository records.
- **setup_oidc_org** — Create/update an organization with OIDC (Okta) auth config.
- **ensure_superuser** — Create or promote a user to superuser.


## Local execution (default — use unless user explicitly says "prod")

Do not ask the user "local or prod?" — just run locally. They'll say "prod" when they mean prod.


```bash
uv run manage.py <command> [args...]
```

## Production execution (only when user explicitly says "prod" / "production")

Via `infra_devopshero/prod_manage.sh` (runs on the ECS container). See the `prod-manage` skill for setup.

```bash
cd infra_devopshero
./prod_manage.sh <command> [args...]
# e.g. ./prod_manage.sh doh_query Deployment status --filter status=failed --limit 10
```