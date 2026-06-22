# Shared (org-provided) integration credentials

## Problem

Today every Hermes user supplies their own third-party API keys (OpenRouter, etc.)
by pasting them into the per-app integrations panel; they are stored as
`IntegrationUserCredential` rows keyed `(owner_user, environment, app_slug, provider)`
and handed to the in-VPC Hermes broker on demand via `POST /api/integrations/tokens`.

We want an **administrator to provision a credential once on the Control Plane** and
**choose who receives it**, so the Hermes agents of the chosen users get it
automatically — no per-user paste. Sharing targets:

1. A particular user
2. A workspace (every Hermes app in that workspace)
3. Everybody in the org

## Locked decisions

1. **Org wins (locked/mandatory).** When both a shared credential and a personal
   one apply, the shared one is used; the personal key is shadowed, not deleted.
2. **Workspace = every app in the workspace** (resource-side match against the
   requesting app's workspace, not "users in the workspace" — users don't *belong*
   to a workspace, they hold access levels to it, which is ambiguous and a footgun).
3. **Org-wide** scope only for v1 (no per-environment narrowing).
4. **Plaintext at rest** for v1, mirroring `IntegrationUserCredential` (JSON in
   Postgres). Acknowledged debt; revisit with Secrets Manager later.
5. **Vault providers only**, starting with **OpenRouter** (already TLS-intercepted
   by the broker). OAuth providers are out of scope.

Derived decisions:

- **A — Specificity.** When multiple shared credentials match one `(user, app, provider)`,
  most-specific wins: **user > workspace > everyone**. Selection lives in the resolver,
  over the set ABAC permits.
- **B — `$app` token.** Authorization is **full ABAC**. The workspace match needs the
  requesting *app* as evaluation context, which the two-sided (identity × resource)
  engine cannot express. We extend the matching language with a third **referenceable**
  side, `$app.<key>` — not a third conditions list, so the `Policy` schema is unchanged.
- **C — Propagation.** Admin changes reach brokers eventually, within one broker cache
  TTL. No push-invalidation in v1.

## Authorization model

`IntegrationSharedCredential` becomes a new ABAC resource type `credential` with one
action `credential:use`. The admin's "share with" choice is stored in canonical columns
(`scope` + `target_workspace`/`target_user`), which **seed `ResourceTag`s** exactly like
`App.name` seeds the `app-name` system tag today. The authorization decision runs entirely
through `filter_permitted_resources(..., "credential", "credential:use", app_context=app)`.

Seeded tags per credential:

- always `shared-scope=<scope>`
- user scope: `shared-user=<target_user.username>` (username is immutable)
- workspace scope: `shared-workspace=<target_workspace.slug>`

The workspace match reuses the existing `workspace-name` tag, whose **value is the
slug** (`create_default_workspace_tag`), so no new tag is introduced on the workspace side.

Three system policies (seeded at org bootstrap, `resource_type="credential"`):

1. **Everyone** — `resource:[shared-scope=everyone]` -> `credential:use`
2. **User** — `identity:[username=$resource.shared-user]`, `resource:[shared-scope=user]` -> `credential:use`
3. **Workspace** — `resource:[shared-workspace=$app.workspace-name]` -> `credential:use`

## The `$app` engine extension

The engine (`humanityrules_app/services/abac_service.py`) is two-sided: identity
attributes x resource tags, with cross-side references `$identity.<k>` / `$resource.<k>`
resolved against the opposite side's `{key: {values}}` map.

`$app` adds a third side that is **read-only** — referenceable from either conditions
list, but with no conditions list of its own (so `Policy` keeps just
`identity_conditions` + `resource_conditions`). Its data is the requesting app's
effective tags (`get_effective_tags(org, app, "app")`, which already folds in inherited
workspace tags). When no app context is supplied, `$app.*` fails closed, consistent with
how `$resource.*` fails closed in unscoped evaluation.

Touch points:

- `_REFERENCE_PATTERN`: add `app` to the alternation.
- `_conditions_match`: generalize the single `other_side_*` params into a
  `{side_label: side_map}` dict.
- `_validate_references`: a conditions list may reference any side but its own.
- `evaluate_policies`, `check_action`, `filter_permitted_resources`: gain a mandatory
  `app_context: App | None` parameter (house style forbids defaults), so every existing
  caller passes `app_context=None`. `evaluate_policies_unscoped` does not take it.

## Broker resolution

In `integrations_tokens_batch` (`POST /api/integrations/tokens`):

1. Load `app = App.objects.filter(organization=org, slug=app_slug).first()` once.
2. Per requested provider, resolve a shared credential first (org wins):
   - candidates = `IntegrationSharedCredential(org, provider)`
   - filter via `filter_permitted_resources(..., "credential", "credential:use", app_context=app)`
   - pick by specificity user > workspace > everyone
   - if found, package via the provider's `refresh_outcome_from_shared(cred)` and return `has_token`
3. Otherwise fall back to the existing personal `refresh_outcome`.

`refresh_outcome_from_shared` lives on each vault provider module so the secret shape and cache TTL
stay with the provider. OpenRouter returns `has_token(secrets={"api_key": ...},
expires_in=OPENROUTER_BROKER_CACHE_SECONDS, ...)`.

## Admin UI / user panel

- Org-admin-gated admin surface (via `require_org_admin`) to CRUD shared credentials:
  provider, key (validated with the provider's live check), scope + target. The
  OpenRouter validation (`_openrouter_current_key`) is shared between personal and
  shared writes.
- The per-user integrations panel shows an applicable shared credential read-only
  ("Provided by your organization") and disables the paste form, since shared wins.

## Migrations / rollout

- New `IntegrationSharedCredential` model; `ResourceTag.credential` FK + updated
  `resource_tag_exactly_one_fk` check constraint; `"credential"` added to
  `ResourceTag.ResourceType` and `Policy.ResourceType` choices.
- Data migration backfilling the three system policies into existing orgs.
- Propagation is eventual via the broker cache TTL.
- Plaintext-at-rest is acknowledged v1 debt.

## Implementation status (v1, shipped)

Backend is complete and tested:

- `IntegrationSharedCredential` model + `ResourceTag.credential` FK + `credential`
  resource type on `ResourceTag`/`Policy` (migration `0003`); credential policies
  backfilled into existing orgs (migration `0004`) and seeded for new orgs in
  `bootstrap_organization`.
- `$app` reference token in the ABAC engine: generalized `_conditions_match` to a
  multi-side map, "reference any side but your own" validation, and a dedicated
  `filter_permitted_credentials(org, user, app, queryset)` that supplies the app side.
  The public `evaluate_policies` / `filter_permitted_resources` signatures are
  unchanged, so `$app` is currently surfaced only through the credential path.
- `sync_shared_credential_tags` seeds `shared-scope` / `shared-user` / `shared-workspace`
  on every save (post_save signal).
- `shared_credential_resolver.resolve` + `provider_openrouter.refresh_outcome_from_shared`, wired
  into `integrations_tokens_batch` so shared credentials win over personal keys.
- Tests: `tests/test_shared_credentials.py` (engine path, tag seeding, resolver
  precedence, packaging, `$app` validation) and a `TestSharedCredentials` class in
  `tests/test_integrations_tokens_batch.py` (full HTTP path, org-wins override,
  workspace-via-`$app`).

Input surface for v1 is the Django admin (`IntegrationSharedCredentialAdmin`), which
validates scope/target coherence and live-checks OpenRouter keys via
`provider_openrouter.validate_shared_key`.

Deferred (follow-ups):

- Bespoke org-admin-gated security/integrations page (Django admin is superuser-gated,
  not org-admin-gated).
- User integrations panel showing an applicable share read-only ("Provided by your
  organization") and disabling the paste form.
- Surfacing `$app` on `evaluate_policies` if a non-credential consumer needs it.
- Secrets Manager storage; push-invalidation to brokers; non-OpenRouter providers.
