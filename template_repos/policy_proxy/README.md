# HUMR Policy Proxy

A tiny reverse proxy that sits in front of apps deployed by Humanity Rules and enforces SSO + ABAC at the edge. See `docs/policy_proxy_design.md` for the full design.

## What it does, per request

1. Reads the `humr_session` cookie.
2. If missing or invalid, 302s to `https://humanityrules.io/auth/env-start?rd=<current-url>` for the OAuth dance. The control plane mints a session JWT and bounces back to `/__humr_session_install?token=...&rd=...`, which sets the env-scoped cookie and redirects the browser to `rd`.
3. Verifies the JWT against the central JWKS (fetched from `/.well-known/jwks.json`, cached 15 min). The `aud` claim must match `HUMR_ENV_DOMAIN` to block cross-env replay (the env's DNS zone is globally unique; `HUMR_ENV_SLUG` is only unique per AWS account).
4. POSTs to HUMR's PDP endpoint with `{provider, sub, username, path}` and `Authorization: Bearer <HUMR_APP_BEARER>`. The bearer identifies the app; nothing in the body does.
5. On `allow`, proxies to the app container on localhost, injecting trusted identity headers.
6. On `deny`, returns a 403 with a short message.

For API/fetch-style requests with missing or invalid auth, step 2 returns a
same-origin `401` with `X-HUMR-Auth-URL` instead of a `302`. Browser navigation
requests still receive the `302` directly.

## Config (all env vars; no defaults)

| Var | Example | Purpose |
| --- | --- | --- |
| `HUMR_APP_ID` | `vmendihermes` | App slug, for log lines and the decision-cache key. Not sent to the control plane. |
| `HUMR_ENV_SLUG` | `humr-sandbox` | For log lines only. |
| `HUMR_ENV_DOMAIN` | `humr-sandbox.humrsandbox.com` | Parent domain the session cookie is scoped to. |
| `HUMR_AUTH_BASE_URL` | `https://humanityrules.io` | Control-plane base URL; the sidecar bounces unauthenticated requests to `<base>/auth/env-start`. |
| `HUMR_CONTROL_PLANE_URL` | `https://humanityrules.io` | Control-plane base URL for runtime activity reports. |
| `HUMR_JWKS_URL` | `https://humanityrules.io/.well-known/jwks.json` | Central JWKS endpoint for verifying session JWTs. |
| `HUMR_PDP_URL` | `https://humanityrules.io/api/pdp/evaluate` | Central authorization endpoint. |
| `HUMR_APP_BEARER` | 64 random chars | This app's bearer token. From the app's own Secrets Manager bag (`humr/{env}/{app}/secrets`). Presenting it proves which app is calling. |
| `HUMR_UPSTREAM_HOST` | `127.0.0.1` | The app container. |
| `HUMR_UPSTREAM_PORT` | `8787` | The app container's port. |
| `HUMR_LISTEN_PORT` | `8443` | Port the policy proxy listens on. ALB routes here. |
| `HUMR_PUBLIC_HOSTNAME` | `vmendihermes.humr-sandbox.humrsandbox.com` | Agent's public hostname. When set, Hosts of the form `<webapp-slug>-<hostname>` get the anonymous public-grant check. Optional: unset (e.g. local runs) means no Host is a webapp. |
| `HUMR_PDP_CACHE_TTL_SECONDS` | `60` | PDP decision cache TTL. Optional, default 60. |
| `HUMR_PUBLIC_CACHE_TTL_SECONDS` | `10` | Public-grant decision cache TTL; bounds both grant and revocation latency. Optional, default 10. |
| `HUMR_POLICY_PROXY_ACTIVITY_INTERVAL_SECONDS` | `300` | Coalescing interval for activity reports. Optional, default 300. |

## Local development

```bash
cd template_repos/policy_proxy
uv run python -m policy_proxy.main  # relies on the env vars above
```

For unit tests:

```bash
cd template_repos/policy_proxy
uv run pytest
```

## Publishing

The policy-proxy image is published to ECR per environment — `humr/{env_slug}/policy-proxy:{version}` — from the HUMR deploy pipeline on first policy-proxy deploy. See `humanityrules_app/services/infra_customer/` for the push plumbing.
