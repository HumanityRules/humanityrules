# App Removal: Data Cleanup Audit

Snapshot: 2026-05-24. Captured during a `tls_intercept` refactor conversation.

When a user removes an App through the UI or CLI, an `AppRemovalJob` is queued
and the executor runs an opt-in cleanup. The current set of flags doesn't
fully describe everything tied to an app's lifetime, and one critical class
of data is **never cleaned up at all**.

## Today's flags on `AppRemovalJob`

Three independent booleans (UI shows them as separate checkboxes in
`_app_remove_confirm_modal.html`):

1. **`delete_persistent_data`** — wipes EFS `/deployments/{app_slug}/` *and*
   EC2 host bind-mount paths from `template.containers[*].host_mounts`.
   Despite the name, covers two physical surfaces (was renamed from
   `delete_efs_data` in migration 0054). Implemented in
   `app_remove_executor._run_efs_cleanup_task` and
   `_run_host_path_cleanup_ssm`.

2. **`delete_secrets`** — calls
   `secrets_utils.delete_secrets_matching_prefix` against the customer
   account's AWS Secrets Manager, scoped to
   `humr/{env_slug}/{app_slug}/`. These are per-app secrets injected
   at deploy time (DB passwords from the AppTemplate `secrets` block, etc.).

3. **`delete_policies`** — wipes ABAC `Policy` rows referencing the app slug
   in the DOH control-plane DB.

## What is NOT cleaned up: `IntegrationUserCredential`

`IntegrationUserCredential` rows live in the **DOH control-plane DB** and store:

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
and Telegram bot tokens orphaned in DOH's database forever. If a future app
is created with the same slug under the same `(owner_user, environment)`
tuple, those rows match again and the integration appears "magically
connected" — surprising and almost certainly wrong.

This is also a GDPR-shaped problem: a user clicking "remove" reasonably
expects their stored credentials to go with the app.

## Conceptual surfaces (mental model the user has)

Three categories, but only two are exposed:

| Surface | Where | Exposed? | Flag |
|---|---|---|---|
| App data | EFS + host mounts (customer account) | Yes | `delete_persistent_data` |
| Customer-account secrets | AWS Secrets Manager (customer account) | Yes | `delete_secrets` |
| Control-plane integration credentials | DOH DB (`IntegrationUserCredential`) | **No** | — |
| ABAC policies | DOH DB (`Policy`) | Yes | `delete_policies` |

## Recommendations

1. **Always delete `IntegrationUserCredential` on app removal**, no opt-in
   flag. The credentials only made sense in the context of this app; the
   default should be to clean them up. Implementation is one query in
   `app_remove_executor`:

   ```python
   IntegrationUserCredential.objects.filter(
       app_slug=app.slug,
       environment__in=environments,
   ).delete()
   ```

   If we ever need a "keep credentials" escape hatch, it would be the rare
   path, not the default.

2. **Consider collapsing `delete_persistent_data` and `delete_secrets`**
   into a single user-facing flag (e.g. `wipe_all_data`). The user's mental
   model is "delete everything related to this app" — there's little reason
   they'd want to keep customer-account secrets but drop EFS data, or vice
   versa. Internally the executor still walks both surfaces.

3. **Rename `delete_persistent_data` if it stays separate.** "Persistent
   data" reads as "EFS volumes" in conversation, but the flag also covers
   host bind-mounts. A name like `delete_app_storage` or
   `wipe_filesystem_state` would be more honest.

## Out of scope (related but not this audit)

- `delete_policies` — ABAC behavior is its own thing, not part of the
  data-cleanup category.
- Conversation `context_app` — already handled via `SET_NULL`, which is
  correct (preserve transcript history, drop the link).
- Organization / workspace teardown — different lifecycle entirely.

## Action items

- [ ] Add unconditional `IntegrationUserCredential` cleanup to
  `app_remove_executor.run_app_removal`.
- [ ] Decide on flag consolidation vs. rename.
- [ ] Update `_app_remove_confirm_modal.html` accordingly.
- [ ] Add a regression test in
  `humanityrules_app/tests/` that creates an `IntegrationUserCredential` then
  removes the app and asserts the row is gone.
