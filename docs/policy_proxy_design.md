# Policy Proxy Design

The policy proxy enforces authentication and ABAC authorization for apps deployed by DOH. It is the runtime counterpart to the ABAC model described in `authorization_design_abac.md` — it sits in front of each protected app, resolves the caller's identity via SSO, asks DOH's PDP for a decision, and either proxies or rejects the request.

The first use case is the Personal Assistant (Hermes) deployment, where each employee gets a personalized URL (e.g. `vmendi-hermes.chsandbox.com`) and only the owner plus system admins may access it.


## Scope

- Opt-in per AppTemplate. Default is off. When on, the deployed ECS task includes a policy proxy container in front of the app container.
- Single shared policy proxy image published by DOH. AppTemplates do not embed their own copy.
- v1 target: Hermes PA. Design generalizes to any HTTP app behind ALB → ECS.

Out of scope for v1:

- WebSocket protection (ALB → Lambda restriction is not relevant to the policy proxy itself; noted for the auth endpoint below).
- Session revocation lists.
- Offboarding / orphaned-stack cleanup.
- Hardening the `owner` tag against edits post-deploy (see deferred list).


## Components

Three pieces, all living inside the customer's AWS account except the PDP:

- **Auth Service** — one per environment. Singleton ECS Fargate service running the policy-proxy image in `DOH_ROLE=auth` mode. Handles OAuth with the customer's Okta tenant, mints session JWTs. Fronted by the env ALB via a host-based listener rule (`auth.<env-domain>`).
- **Policy Proxy container** — one per protected app instance, in the same ECS task as the app container. Verifies JWTs locally, calls the DOH PDP per request (with caching), proxies or rejects.
- **DOH PDP endpoint** — on the DOH control plane. Evaluates ABAC policies. Called by policy proxies over HTTPS.


## Per-Environment Trust Anchors

Two secrets are provisioned when an environment is created:

- **Policy-proxy JWT keypair** — RSA or EdDSA. Private half kept by the auth service. Public half served at `https://auth.<env-domain>/.well-known/jwks.json`. Stored in Secrets Manager at `devopshero/{env-slug}/policy-proxy-auth-config` under the `jwt_key` key (alongside `oidc_config`, which carries the Okta app credentials for the same service).
- **Environment bearer token** — random 64-char bearer token. Stored in the shared-per-env secret `devopshero/{env-slug}/shared-secrets` under key `DOH_ENV_BEARER`. Any env-resident component that calls DOH's control plane (policy proxies today; Hermes and other future services) reads it and sends it on every call. Rotated by redeploying the env's policy proxies.

Both are auto-generated at env bootstrap. No manual provisioning.


## Auth Service

Responsibilities:

- Start the OAuth flow (`/start`), passing the original destination URL through the `state` parameter.
- Handle the OAuth callback (`/callback`), exchange the code with Okta, mint a JWT, set the cookie.
- Serve the JWKS (`/.well-known/jwks.json`) so policy proxies can verify signatures.

Runtime shape:

- **Deployment:** Fargate service (1 task, 0.25 vCPU / 0.5 GB RAM, ARM64), running the same `doh/{env-slug}/policy-proxy` image as the sidecars but with `DOH_ROLE=auth`. Fronted by the env's shared ALB via a host-based listener rule (`host-header = auth.<env-domain>` → forward → IP target group → container port 8443). ALB health check hits `/__policy_proxy/healthz`, which is served without auth.
- **Provisioning trigger:** lazy. Deployed on first policy-proxy-enabled app deploy in the env. Subsequent policy-proxy'd apps reuse it.
- **Rolling deploys:** zero-downtime via `minHealthyPercent=100`, `maxHealthyPercent=200` — ECS brings up a new task and drains the old one before replacing it. Brief extra cost during deploys only.
- **Okta configuration:** the Okta app for this env has exactly one registered redirect URI: `https://auth.<env-domain>/callback`. Per-user destination URLs are carried in `state`, not in the redirect URI — no per-user whitelist.

JWT shape (session cookie):

```json
{
  "sub": "<oidc_sub>",
  "username": "<username>",
  "email": "<email>",
  "iat": 1700000000,
  "exp": 1700003600
}
```

- TTL: 1 hour. On expiry, policy proxy redirects back through the auth flow; Okta typically short-circuits the login silently.
- No attributes in the cookie. Authorization is resolved per-request against DOH, which always sees the current attribute state.
- Signed with the env's private key from Secrets Manager.
- Set as `Set-Cookie: doh_session=<jwt>; Domain=.<env-domain>; Secure; HttpOnly; SameSite=Lax; Path=/`. Parent-domain scope means all policy-proxy'd apps in the env read it with one login.

Redirect validation:

- The `rd` URL unpacked from `state` must have a host ending in the env's parent domain. Reject otherwise — prevents open-redirect abuse.


## Policy Proxy Container

Runtime shape:

- **Image:** published to ECR by DOH. Distribution shape (single DOH-owned ECR with cross-account pull vs per-customer-account mirror via the existing `build_and_push_docker_image` flow) is unresolved — see the implementation plan.
- **Placement:** same ECS task as the app container. Policy Proxy listens on the task's public port; app container listens on localhost. Task definition is produced by DOH when the AppTemplate opts in.
- **Opt-in flag:** AppTemplate declares `policy_proxy: true` (declared via a container entry) (or equivalent). Without it, the task is deployed without a policy proxy and retains its existing behavior.

Request flow:

1. Request arrives at the policy proxy.
2. If the path is an internal policy proxy path (e.g. `/__policy_proxy/healthz`), handle locally.
3. Read the `doh_session` cookie. If missing or invalid (bad signature, expired), 302 to `https://auth.<env-domain>/start?rd=<current-url>`.
4. Verify JWT signature against the cached JWKS. Extract `sub`, `username`.
5. Look up `(oidc_sub, app_id, path-pattern)` in the decision cache.
6. On cache miss, call the DOH PDP (see below). Cache the result with a 600 s TTL.
7. On stale-on-error (DOH unreachable and cache entry expired), fail closed — return 503 with a short explanation. Existing sessions with warm cache entries keep working for the duration of their TTL.
8. On allow, proxy to the app container on localhost. On deny, return a 403 with a message distinguishing "you are not the owner" from "the app does not exist."

What the policy proxy forwards to the app:

- Identity headers injected after successful decision. Exact header contract to be determined by the spike on the Hermes WebUI image (see open items). Placeholder set: `X-Auth-User: <username>`, `X-Auth-Sub: <oidc_sub>`, `X-Auth-Email: <email>`.
- Original `Host` preserved via `X-Forwarded-Host`.

JWKS handling:

- Fetched at startup from `https://auth.<env-domain>/.well-known/jwks.json`.
- Refreshed periodically (e.g. every 15 min) and on verification failure with an unknown `kid`.


## DOH PDP Endpoint

Endpoint: `POST https://humanityrules.io/api/pdp/evaluate`

Request:

```json
{
  "app_id": "vmendi-hermes",
  "oidc_sub": "00u1a2b3c4...",
  "username": "vmendi",
  "path": "/chat/new"
}
```

Headers: `Authorization: Bearer <DOH_ENV_BEARER>`.

- The token authenticates the caller as a legitimate component inside a customer env. Shared per env, since all env-resident components sit inside the same trust boundary.
- The `app_id` is self-reported by the policy proxy. Inside a trusted env, this is acceptable.

Response:

```json
{
  "decision": "allow",
  "reason": "policy:pa-owner-access"
}
```

or:

```json
{
  "decision": "deny",
  "reason": "no-matching-policy"
}
```

DOH logic:

1. Resolve the token → env → list of apps in that env. Confirm `app_id` belongs to that env.
2. Load the app's effective tags (direct + inherited from workspace).
3. Load the identity's effective attributes (direct + group-inherited + system), using `oidc_sub` as the lookup key.
4. Evaluate ABAC policies for the app. Include route overrides for `path`.
5. Return allow or deny plus the ID of the granting/denying policy.

Logging: `logging.info` per decision with `{timestamp, username, oidc_sub, app_id, path, decision, policy_id}`. No separate audit store in v1.

Failure mode: policy proxy treats non-2xx responses and timeouts as "unable to decide" → stale cache falls back when present; otherwise fail closed.


## Personal Assistant Specifics

Deploy-time wiring:

- AppTemplate "Hermes Agent" declares `policy_proxy: true` (declared via a container entry).
- AppTemplate sets `app-type = personal-assistant` as an immutable system tag on the app.
- The "New Personal Assistant" deploy form includes an **Owner** field alongside Workspace and Environment:
  - Non-admins see it prefilled to `self` and locked — self-serve only.
  - Admins see a user search/dropdown and can deploy on behalf of another employee.
- At deploy time, DOH sets the `owner` tag on the app to the owner's `username`, validated server-side against the User table. The form value is not trusted.
- The subdomain (`<slug>-hermes.<env-domain>`) is baked at deploy time and never changes, even if the user's `username` later changes.

Authorization:

- One global seed policy installed per org on first use of a policy-proxy'd PA:
  - **Identity condition:** `username = $resource.owner`
  - **Resource condition:** `app-type = personal-assistant` (on app)
  - **Actions:** `app:use`
- System admins receive access via their existing org-admin policies.
- No per-PA policy is created. All PAs share the one self-referential rule.

Identity anchor:

- `username` is the ABAC-facing identifier. Treated as stable and immutable for the purposes of this feature; any future UI that mutates it must be gated.
- `oidc_sub` is carried in the JWT and request payload for audit robustness.

Slack gateway:

- The Slack gateway runs alongside the WebUI inside the Hermes container and is **not** behind the policy proxy. Slack access is governed by Slack workspace membership and token scoping — a separate trust path. Out of scope for the policy proxy.

`HERMES_WEBUI_PASSWORD`:

- Dropped from the Personal template's runtime variables. With no env var set, the upstream WebUI's own password auth is disabled (`api/auth.py` reads it directly from process env; empty/unset → `is_auth_enabled()` returns False). The policy proxy is the only gate. The Slack template still sets it, since that deployment isn't behind the policy proxy.
- Complemented by `HERMES_WEBUI_HOST=127.0.0.1` in `template_repos/hermes_agent/doh_runtime/supervisor.sh` so the WebUI binds loopback-only on :8789. Both containers share the task ENI in Fargate awsvpc; without this override the upstream image default `0.0.0.0` would leave the WebUI reachable on the task ENI.


## Opt-in Wiring

AppTemplate-level flag (working name: `policy_proxy: true` (declared via a container entry)) controls:

- Whether DOH produces a task definition with two containers (policy proxy + app) or one.
- Whether the ALB listener rule routes to the policy proxy port (always the case when policy proxy is on).
- Whether the deploy form adds the Owner field (currently only for `app-type = personal-assistant`; other policy-proxy'd templates do not need Owner).

The policy proxy does not assume a specific app; it reads its `app_id` and `listen_port`/`upstream_port` from environment variables set by DOH at deploy time.


## Failure Modes Summary

- **Okta down:** new logins fail. Existing JWTs keep working until `exp`.
- **Auth Service down:** new logins fail. Existing JWTs keep working.
- **DOH PDP down:** existing sessions with warm cache entries keep working for 60 s. Cache expiry → fail closed (503).
- **Policy Proxy crash:** ECS restarts the task. Cold start re-fetches JWKS; decision cache is empty until populated.
- **JWT signing key compromise:** rotate by generating a new keypair in Secrets Manager, force a new auth-service deployment so it picks up the new key, policy proxies pick up the new public key via JWKS refresh. All existing sessions invalidated.


## Open Items

- **Hermes WebUI header contract.** The exact upstream-auth header names and whether Hermes WebUI supports them out of the box (or needs a patch) is unresolved. Deferred.
- **Self-referential conditions in the engine.** The ABAC evaluation engine (`devopshero_app/services/abac.py`) already exists and is used elsewhere, but the `$identity.<key> = $resource.<key>` condition form introduced in `authorization_design_abac.md` needs to be added. Small extension — a new clause type plus evaluator branch.
- **PDP HTTP endpoint.** The in-process engine exists; wrapping it as the `POST /api/pdp/evaluate` endpoint with shared-env-token auth and the request/response shape above is new work.
