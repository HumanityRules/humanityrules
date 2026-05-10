# Google Workspace Integration Design

How a Hermes Personal Assistant user connects their Google account so the agent can act on their behalf, without any long-lived Google credential ever entering a customer environment.

## The core constraint

A Hermes agent runs inside a customer's AWS account, inside a nono sandbox, on behalf of one specific user. It needs to call Gmail/Calendar/Drive/etc. on that user's behalf. This requires an OAuth refresh token somewhere, long-lived enough to mint short-lived access tokens for the lifetime of the connection.

**The architectural question is where that refresh token lives.** Three plausible homes:

1. **Customer env Secrets Manager.** The sandbox-adjacent supervisor refreshes it. Rejected: puts DOH's OAuth client secret into every customer env too, and a compromised env yields long-term Gmail access.
2. **DOH control plane.** Env-resident components call DOH to get a fresh access token. Chosen.
3. **Inside the sandbox (with Google's SDK doing its own refresh).** Rejected for the same reasons as (1), amplified — the sandbox itself becomes the credential custodian.

Choice (2) is the design. It costs a runtime dependency on DOH's control plane — if DOH is unreachable for ~an hour, Gmail-dependent tools stop working. That's an acceptable tradeoff for custody.

## The trust boundary

Only one secret crosses from DOH into a customer env: a **short-lived Google access token** (minutes of validity). Nothing else Google-related is stored in the env, not even transiently.

The refresh token, DOH's OAuth client secret, and every other long-lived Google credential live only in DOH's database.

The sandbox sees only the access token, via a file the platform maintains. It cannot refresh on its own — that would require credentials it doesn't have.

## Three actors, three roles

**The browser (user on their laptop).** Drives the initial OAuth consent. Traverses DOH's control plane to authenticate the user, then Google's consent screen, then DOH's callback. Lands back on the Hermes WebUI with the connection recorded.

**DOH's control plane.** Owns the Google OAuth client, stores refresh tokens per (user, env, provider), and exposes an authenticated endpoint that mints access tokens. Also the identity authority — a DOH user is the unit Google grants are attached to, and Hermes users are by construction DOH users.

**The customer environment.** Has a supervisor process (outside the sandbox) that periodically asks DOH for a fresh access token and writes it to a known path. The sandbox reads that path read-only via nono's filesystem grant; everything else is invisible to it.

## Authentication between the three

- **Browser → DOH control plane:** standard OIDC/Okta login. The connect link carries a validated `next` parameter that lands the user on `/integrations/google/start` post-auth.
- **DOH control plane → Google:** DOH's OAuth client, registered with Google, with redirect URIs covering every host DOH can be reached at (prod, ngrok for dev). The right URI is chosen per request based on the browser's current host.
- **Customer env supervisor → DOH control plane:** the per-environment bearer token that already authenticates the policy-proxy's PDP calls. Env-scoped, not user-scoped; the user identity is passed in the request body and validated server-side against the env's org. The supervisor has the bearer; the sandboxed agent does not.
- **Sandbox → supervisor:** no direct channel. The sandbox reads a file; that file is the whole API.

## Why `rd` validation sits where it does

The connect flow accepts a `?rd=<url>` pointing at the Hermes WebUI, which the user lands on after the dance completes. Validating `rd` pre-auth would leak which hostnames correspond to real environments — an anonymous caller could probe DOH's tenancy. So `rd` is validated inside the authenticated start view, against the list of known env domains.

## Failure modes and their shape

- **User hasn't connected Google yet.** Refresh endpoint returns 404. Supervisor removes the token file; sandbox sees no file and surfaces a "not connected" message to the user.
- **Refresh token revoked at Google** (either by the user or by Google's security systems). Refresh endpoint returns 410 and deletes the stored grant; user must reconnect. This is the standard revocation path.
- **Transient Google failure.** Supervisor keeps the last valid access token until it expires, retries with backoff. Tool calls fail only once the cached token actually expires.
- **DOH control plane unreachable.** Same as transient failure at first; if DOH stays down past the current access token's expiry, Google tool calls start failing. This is the cost of option (2).
- **Customer env compromised.** The attacker gets the bearer, which can mint access tokens for users already connected in that env — bounded to env users, bounded in token lifetime. They cannot extract refresh tokens (they're not in the env) and cannot mint for other envs (the bearer is env-scoped).

## What lives where

- **OAuth client ID + secret** — DOH database. One per DOH deployment. Never crosses the boundary.
- **Refresh tokens** — DOH database, keyed by (user, env, provider). Custody stays on DOH. Scoped to env so revocation can be env-surgical.
- **Env bearer token** — customer env secrets manager, with only the hash stored on DOH. Pre-existing pattern, reused.
- **Access token** — customer env, single file written by the supervisor. The only Google-side credential the env ever holds. Short-lived.

## Shape that generalizes to other providers

Everything above is Google-specific only in naming. The same architecture — provider config on DOH, per-user grants on DOH, refresh endpoint keyed on env bearer + user claim, supervisor-side refresher, sandbox reads a file — works for Slack, Notion, or anything else with an OAuth refresh-token flow. Schema (`UserThirdPartyIntegration`) and endpoint shape are already provider-generic.

## Open questions

- **Governance.** How does "admin hasn't enabled Gmail for this workspace" get expressed? Probably a workspace-level allowlist checked before `/integrations/google/start` proceeds. Not built.
- **Revocation surfacing.** When DOH detects revocation via 410 during a refresh, the connection silently disappears from the UI. The Connections page should distinguish "never connected" from "was connected, now revoked" so the user knows why.
- **Scope granularity.** Currently all-or-nothing per app family. If customers ask for read/write splits ("the agent can read mail but not send"), we'd add per-scope toggles on the Connections page and track granted scopes more narrowly in the grant row.
- **Multi-account.** A user might want to connect two Google accounts (personal + work). The schema allows it in principle (drop the `(user, env, provider)` uniqueness); the UX and routing logic — "which account does this tool call use?" — are not designed.
- **Encryption at rest.** Refresh tokens are plaintext in Postgres, matching existing posture. A cross-cutting initiative would cover them uniformly; not a per-column decision.
