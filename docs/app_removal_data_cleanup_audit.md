# App Removal: Data Cleanup Audit

Snapshot: 2026-05-24. Captured during a `tls_intercept` refactor conversation.
Model references updated for the Deployment-collapse refactor (removal state now
lives on the App row, and the three cleanup flags collapsed into one).

When a user removes an App through the UI or CLI, a removal attempt is queued on
the App (`app_job_service.queue_removal` sets `job_status` → removal_pending and
records the removal inputs), and the executor runs the cleanup. The single
`delete_all_data` flag doesn't fully describe everything tied to an app's
lifetime, and one critical class of data is **never cleaned up at all**.

## Today's removal inputs (fields on `App`)

`AppRemovalJob` is gone; removal is driven by two App fields set at enqueue:

- **`removal_teardown_first`** — when set, the executor tears down the app's
  live infra inline before cleanup; removal is otherwise refused while
  `may_have_infra` is set. The UI's "Remove App" and the CLI's
  `teardown-app --remove-app` set this.
- **`removal_delete_all_data`** — a single user-facing flag (one checkbox
  `name="delete_all_data"` in `_app_remove_confirm_modal.html`) that, when set,
  drives all three cleanup surfaces at once (recommendation #2 below landed):

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

  Sandbox apps force `removal_delete_all_data` on regardless of the checkbox,
  because the released sandbox slug must never leave inheritable data behind.

## What is NOT cleaned up: `IntegrationUserCredential`

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

**Net consequence:** removing an app today leaves user OAuth refresh tokens
and Telegram bot tokens orphaned in HUMR's database forever. If a future app
is created with the same slug under the same `(owner_user, environment)`
tuple, those rows match again and the integration appears "magically
connected" — surprising and almost certainly wrong.

This is also a GDPR-shaped problem: a user clicking "remove" reasonably
expects their stored credentials to go with the app.

## Conceptual surfaces (mental model the user has)

Three categories, but only two are exposed:

| Surface | Where | Exposed? | Flag |
|---|---|---|---|
| App data | EFS + host mounts (customer account) | Yes | `removal_delete_all_data` |
| Customer-account secrets | AWS Secrets Manager (customer account) | Yes | `removal_delete_all_data` |
| Control-plane integration credentials | HUMR DB (`IntegrationUserCredential`) | **No** | — |
| ABAC policies | HUMR DB (`Policy`) | Yes | `removal_delete_all_data` |

## Recommendations

1. **Always delete `IntegrationUserCredential` on app removal**, no opt-in
   flag. The credentials only made sense in the context of this app; the
   default should be to clean them up. Implementation is one query in
   `app_remove_executor`:

   ```python
   IntegrationUserCredential.objects.filter(
       app_slug=app.slug,
       environment=app.environment,
   ).delete()
   ```

   If we ever need a "keep credentials" escape hatch, it would be the rare
   path, not the default.

2. **Collapse the cleanup flags into one — LANDED.** The three checkboxes are
   now the single `removal_delete_all_data` flag, which drives persistent data,
   customer-account secrets, and ABAC policies together. This also subsumed the
   old recommendation to rename `delete_persistent_data` (that field is gone).

## Out of scope (related but not this audit)

- ABAC policy deletion (the third surface of `removal_delete_all_data`) — ABAC
  behavior is its own thing, not part of the data-cleanup category.
- Organization / workspace teardown — different lifecycle entirely.

## Action items

- [ ] Add unconditional `IntegrationUserCredential` cleanup to
  `app_remove_executor.run_removal`.
- [x] Flag consolidation — done (single `removal_delete_all_data`).
- [ ] Add a regression test in
  `humanityrules_app/tests/` that creates an `IntegrationUserCredential` then
  removes the app and asserts the row is gone.
