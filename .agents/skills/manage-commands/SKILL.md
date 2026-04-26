---
name: manage-commands
description: Understand and use available DOH's management commands, used to manage the plaform, and to troubleshoot, debug, or run tests. If you are trying to diagnose, debug, or verify your work, you probably need this skill.
---

# Django Management Commands

All custom commands live in `devopshero_app/management/commands/`.

**To get detailed usage for any command**, read the first 30 lines of its file — every command has a module-level docstring with usage examples and instructions at the top.


## Command Reference

- **doh_query** — Ad-hoc model queries (fields, filters, ordering). Use instead of `shell -c`.
- **doh_control** — Control plane ops: provision/teardown environments; deploy from AppTemplate (`deploy-app-template`), redeploy, teardown, and remove apps (`teardown-app --remove-app` for full cleanup including secrets/EFS/policies).
- **doh_efs_browse** — Browse EFS filesystem via ECS Exec (interactive shell in customer environment).
- **doh_app_logs** — Fetch CloudWatch logs for a customer app (works for running and crashed/stopped tasks), by account + env + app slug.
- **doh_app_shell** — Interactive shell in a deployed customer app container (ECS Exec / SSM), by account + env + app slug.
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