---
name: manage-commands
description: Understand and use Django management commands. Use when you need to run a management command locally or need to know which command does what.
---

# Django Management Commands

All custom commands live in `devopshero_app/management/commands/`.

**To get detailed usage for any command**, read the first 30 lines of its file — every command has a module-level docstring with usage examples and instructions at the top.


## Command Reference

- **doh_query** — Ad-hoc model queries (fields, filters, ordering). Use instead of `shell -c`.
- **doh_control** — Control plane ops: create/teardown environments, retry deployments.
- **doh_raw** — Direct CDK deployment bypassing UI/DB/job-worker flow.
- **doh_efs_browse** — Browse EFS filesystem via ECS Exec (interactive shell in customer environment).
- **doh_app_logs** — Fetch CloudWatch logs for a customer app (works for running and crashed/stopped tasks), by account + env + app slug.
- **doh_app_shell** — Interactive shell in a deployed customer app container (ECS Exec / SSM), by account + env + app slug.
- **doh_secrets** — Manage customer Secrets Manager secrets: list, purge, and shared environment secrets (shared-list/shared-set/shared-delete). All subcommands take --account and optional --org (name or slug).
- **run_job_worker** — Background job worker for deployments and environment provisioning.
- **seed_test_apps** — Seed orgs, workspaces, environments, and apps for UI testing.
- **seed_test_groups** — Seed test groups with attributes for ABAC testing.
- **seed_test_users** — Seed test users with group memberships.
- **seed_local_repos** — Scan local directory and create Repository records.
- **setup_oidc_org** — Create/update an organization with OIDC (Okta) auth config.
- **ensure_superuser** — Create or promote a user to superuser.


## Local execution

```bash
uv run manage.py <command> [args...]
```

## Production execution

All the management commands above work on production via `infra_devopshero/prod_manage.sh`, which runs them on the ECS container. See the `prod-manage` skill for setup details.

```bash
cd infra_devopshero
./prod_manage.sh <command> [args...]
# e.g. ./prod_manage.sh doh_query Deployment status --filter status=failed --limit 10
```