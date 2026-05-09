# Google Workspace Integration Design

How a Hermes Personal Assistant user connects their Google account (Gmail, Calendar, Drive) so the agent can act on their behalf.

## Scope of v1

- **Personal Hermes WebUI only.** Slack gateway is out of scope for this flow.
- **Per-user OAuth.** Domain-wide delegation deferred.
- **Google-first.** Shape should generalize to other third-party integrations later.

## Decisions

### Integration surface

- **Direct Google SDKs, not an MCP server** (tentative — not finalized). Reason: MCP server tool surfaces bloat the agent context. Revisit if governance story later needs a uniform "approved MCP servers" framing.

### Connection UX

- **Settings-first (not just-in-time).** A "Connections" page in the Hermes WebUI with a card per Google app (Gmail / Calendar / Drive). Chosen for mental-model simplicity and consistency with every other SaaS integration users have seen.
- **Chat behavior when a required app is not connected:** agent replies "I can't read your calendar yet — connect Google Calendar in Settings → Connections." No inline auth card, no auto-retry.
- **Scope bundling:** one card per app, all needed scopes bundled. Splitting within an app (e.g. separate "read Gmail" vs "send mail" toggles) is governance complexity to add later if customers ask.

### OAuth client ownership

- **DOH-owned Google Cloud project, one OAuth client.**
- **Single registered redirect URI:** `https://devopshero.ai/integrations/google/callback`. Google requires exact-match redirects and does not accept wildcards, so per-employee subdomains cannot be registered. The control plane is the fixed landing point; per-env/per-user destinations are carried in `state`.
- **Verification:** Gmail/Drive scopes are "restricted" and require CASA assessment before going to production. Testing mode is sufficient for dev — test users allowlisted explicitly. Verification work starts in parallel, blocks public launch only.
- **Customer-owned client** (enterprise option) is deferred; add if a customer demands it.

### Auth / identity flow on the DOH side

- **Hermes users already have `User` rows in DOH** (created on first Okta login, keyed by `oidc_sub`; required by the PDP at `devopshero_app/views/pdp.py:97`). No separate identity mechanism needed.
- **Control-plane flow, not an auth-service ticket JWT.** Earlier in the design we considered having the env auth-service container mint a signed ticket so DOH could identify the Hermes user without a DOH login. Rejected: Hermes users are already DOH users, so reusing `/oidc/login/` is simpler and avoids a new JWT trust mechanism.
- **Hermes implies the org is OIDC.** The env auth-service (policy-proxy in auth-service role) only speaks Okta — any env hosting Hermes is by construction OIDC. So `/integrations/google/start` can assume OIDC without branching on `Organization.auth_provider`.

### Org migration

- **`setup_oidc_org` already flips `auth_provider` to OIDC** via `update_or_create` (`devopshero_app/management/commands/setup_oidc_org.py:42`). Running it against an existing WorkOS org migrates it. No new command or option needed for v1.
- **Global `LOGIN_URL` (`/auth/login/` → WorkOS) stays as-is.** The integration flow does not rely on `@login_required`'s global redirect; it carries the org slug explicitly.

### The Connect link

- **Hermes-side link:** `https://devopshero.ai/oidc/login/?org=<slug>&next=<URL-encoded /integrations/google/start?rd=<URL-encoded rd>>`
- The org slug is known to Hermes at render time, so no pre-auth lookup on DOH.
- **No env/rd check pre-auth.** Validating `rd` against the `Environment` table before authentication would leak which hostnames are known envs and let anonymous callers probe DOH's tenancy. The `rd` is validated post-auth inside `/integrations/google/start`.

### The `/integrations/google/start` view (DOH side)

- `@login_required`. By the time we're here, the user is authenticated via OIDC.
- Validates `rd`: must be a URL whose host ends in a known env-domain suffix. Rejects otherwise.
- Stashes `rd` plus the authenticated `username` / `oidc_sub` server-side keyed by a fresh random `state`.
- Redirects to Google's `/authorize` with:
  - `client_id` = DOH's one web client
  - `redirect_uri` = `https://devopshero.ai/integrations/google/callback`
  - `scope` = app-bundle scopes for this integration
  - `state` = opaque id
  - `access_type=offline`, `prompt=consent` (first time) to get a refresh token
- **Deferred hardening:** `rd` validation should later check that the authenticated user actually has access to the target env/app, not just "any env exists." Non-issue while one user maps to one org; tighten before that changes.

### The `/integrations/google/callback` view (DOH side)

- Looks up the stashed payload by `state` (recovers `env_slug`, `owner_username`, `rd`).
- Exchanges the code with Google server-to-server (DOH's client secret).
- Upserts a `UserThirdPartyIntegration` row in DOH's DB keyed by `(user, environment, provider="google")` with `refresh_token`, `scope`, and `granted_at`. **No credentials cross into the customer env** — refresh happens later via a DOH endpoint (`POST /api/integrations/google/token`) that env-resident callers hit with the env bearer. This is the option-3 posture: DOH is the sole custodian of refresh tokens, the blast radius for a compromised customer env is zero Google access.
- 302s back to `rd`. Hermes Connections page shows Connected.
- Plaintext at rest (matches existing posture for `Organization.oidc_client_secret` and `IntegrationConfig.config`). Encryption-at-rest is a cross-cutting concern, not a per-column decision.

### Required patches to existing DOH code

- **`/oidc/login/`** (`devopshero_app/views/auth.py:82`): currently hard-redirects to `/dashboard/` when the user is already authenticated. Must honor `?next=<url>` instead.
- **`/oidc/callback/`** (`devopshero_app/views/auth.py:194`): currently always redirects to `/dashboard/`. Must read `next` from session (set during `/oidc/login/`) and redirect there when present.
- **Safe-URL validation for `next`:** use `django.utils.http.url_has_allowed_host_and_scheme`. Without this the `next` param is an open redirector.

### UX wart (accepted for v1)

On cold entry the browser traverses:

```
<app>.<env-domain>
  → devopshero.ai/oidc/login/       (silent if DOH session warm)
  → Okta                             (silent if Okta session warm)
  → devopshero.ai/oidc/callback/
  → devopshero.ai/integrations/google/start
  → accounts.google.com              (first time: consent; later: auto)
  → devopshero.ai/integrations/google/callback
  → <app>.<env-domain>/settings/connections?connected=google
```

If the user's Okta session has expired in the tiny window between the last Hermes interaction and the click, they'll re-enter Okta creds before reaching Google's consent screen. Rare enough to accept.

## Open questions

- **Governance layering.** Where does "admin hasn't enabled Gmail for this workspace" get enforced? Most likely a new model on the DOH control plane, checked before starting the Google dance.
- **Revocation visibility.** If the user revokes on `myaccount.google.com`, the next tool call fails. Settings page should show "Last verified" so the black-box state is legible.
- **MCP vs SDK** is tentative — still worth revisiting when governance surface crystallizes.
