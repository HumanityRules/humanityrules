# App Removal: Data Cleanup Audit

Snapshot: 2026-05-24. Captured during a `tls_intercept` refactor conversation.
Model references updated for the Deployment-collapse refactor (removal state now
lives on the App row) and again when the removal flags were dropped entirely —
removal is now unconditional, so the only remaining question this audit asks is
what removal deliberately leaves behind.

When a user removes an App through the UI or CLI, a removal attempt is queued on
the App (`app_job_service.queue_removal` sets `job_status` → removal_pending),
and the executor runs the whole cleanup. One class of data is **deliberately not
cleaned up**, documented below.

## Today's removal: no inputs at all

`AppRemovalJob` is gone, and so are the two per-attempt flags that replaced it
(`removal_teardown_first` / `removal_delete_all_data`, dropped in migration
`0042_drop_app_removal_flags`). `queue_removal(app, created_by, label)` takes no
options, and `app_remove_executor.run_removal` always performs every step:

  0. **Infrastructure** — when `may_have_infra` is set, `teardown_infra` deletes
     the app's `humr-{env_slug}-{app_slug}-app` CloudFormation stack inline
     before anything is purged.

  1. **Persistent data** — wipes EFS `/deployments/{app_slug}/` *and* EC2 host
     bind-mount paths from `template.containers[*].host_mounts`. Implemented in
     `app_remove_executor._run_persistent_data_purge` (→ `_run_efs_cleanup_task`
     and `_run_host_path_cleanup_ssm`).

  2. **Customer-account secrets** — `_run_secrets_purge` calls
     `secrets_utils.delete_secrets_matching_prefix` against the customer
     account's AWS Secrets Manager, scoped to `humr/{env_slug}/{app_slug}/`.
     These are per-app secrets injected at deploy time (DB passwords from the
     AppTemplate `secrets` block, etc.).

  3. **ABAC policies** — `_delete_matching_policies` wipes `Policy` rows
     referencing the app slug in the HUMR control-plane DB.

  Steps 1 and 2 are packaged as `purge_app_namespace_data`, shared with sandbox
  environment teardown: a released sandbox slug is re-claimable by another org,
  so it must never leave inheritable data behind. That requirement is now simply
  the behavior of every removal.

## What is deliberately NOT cleaned up: `IntegrationUserCredential`

`IntegrationUserCredential` rows live in the **HUMR control-plane DB** and store:

- Google OAuth refresh tokens
- GitHub user-to-server tokens
- Telegram bot tokens (vault paste-style)
- The `allowed_users` config list, scopes metadata, etc.

These rows key on `(owner_user, environment, app_slug, provider)` where
`app_slug` is a plain `SlugField`, **not a foreign key to `App`**. So
`app.delete()` does not cascade them, and the `app_remove_executor` never
queries `IntegrationUserCredential`. Verified by grep: zero references in
`humanityrules_app/services/`.

**Net consequence — accepted by design.** Removing an app keeps the owner's
stored grants, so a user who removes an app and re-creates it with the same slug
finds their integrations already connected. That is the workflow we want.

The risk this was originally filed under does not survive inspection. The rows
are keyed on `(owner_user, environment, app_slug, provider)` with a unique
constraint, and every lookup in `views/integrations/` filters on
`owner_user=request.user` first, so a *different* user creating an app with the
same slug matches none of the original owner's rows. In the shared sandbox each
org has its own `Environment` row (orgs share AWS infra, not the DB row), so a
cross-org slug reuse is a different `environment` FK as well. What remains is
retention, not access control: live refresh tokens and API keys linger in the
control-plane DB after the app they served is gone, until the user disconnects
them or the `owner_user` FK cascades on account deletion. Accepted.

## Conceptual surfaces (mental model the user has)

Everything an app owns is destroyed unconditionally, except the one surface that
is per-user rather than per-app:

| Surface | Where | Removed? |
|---|---|---|
| Deployment infrastructure | CloudFormation (customer account) | Yes |
| App data | EFS + host mounts (customer account) | Yes |
| Customer-account secrets | AWS Secrets Manager (customer account) | Yes |
| ABAC policies | HUMR DB (`Policy`) | Yes |
| Control-plane integration credentials | HUMR DB (`IntegrationUserCredential`) | **No — by design** |

## Out of scope (related but not this audit)

- ABAC policy deletion — ABAC behavior is its own thing, not part of the
  data-cleanup category.
- Organization / workspace teardown — different lifecycle entirely.
- Customer *environment* teardown, which still deletes CFN stacks without
  purging secrets. Its own ticket.

## Action items

- [x] Flag consolidation — done, then finished: both removal flags are gone and
  removal is unconditional.
- [x] `IntegrationUserCredential` cleanup — **closed as accepted by design**.
  The rows are per-user and retained on purpose (reasoning above).
- [x] Regression test pinning the retention, so a future cleanup pass does not
  silently "fix" it:
  `test_app_removal_coordination.test_removal_keeps_the_owners_integration_credentials`.
