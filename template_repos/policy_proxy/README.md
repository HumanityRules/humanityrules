# HUMR Policy Proxy

A tiny reverse proxy that sits in front of apps deployed by Humanity Rules and enforces SSO + ABAC at the edge. See `docs/policy_proxy_design.md` for the full design.

## What it does, per request

1. Reads the `humr_session` cookie.
2. If missing or invalid, 302s to `https://humanityrules.io/auth/env-start?rd=<current-url>` for the OAuth dance. The control plane mints a session JWT and bounces back to `/__humr_session_install?token=...&rd=...`, which sets the env-scoped cookie and redirects the browser to `rd`.
3. Verifies the JWT against the central JWKS (fetched from `/.well-known/jwks.json`, cached 15 min). The `aud` claim must match `HUMR_ENV_DOMAIN` to block cross-env replay (the env's DNS zone is globally unique; `HUMR_ENV_SLUG` is only unique per AWS account).
4. POSTs to HUMR's PDP endpoint with `{app_id, provider, sub, username, path}` and `Authorization: Bearer <HUMR_ENV_BEARER>`.
5. On `allow`, proxies to the app container on localhost, injecting trusted identity headers.
6. On `deny`, returns a 403 with a short message.

For API/fetch-style requests with missing or invalid auth, step 2 returns a
same-origin `401` with `X-HUMR-Auth-URL` instead of a `302`. Browser navigation
requests still receive the `302` directly.

## Config (all env vars; no defaults)

| Var | Example | Purpose |
| --- | --- | --- |
| `HUMR_APP_ID` | `vmendi-hermes` | App slug, sent in the PDP request. |
| `HUMR_ENV_SLUG` | `doh-sandbox` | For log lines only. |
| `HUMR_ENV_DOMAIN` | `doh-sandbox.dohsandbox.com` | Parent domain the session cookie is scoped to. |
| `HUMR_AUTH_BASE_URL` | `https://humanityrules.io` | Control-plane base URL; the sidecar bounces unauthenticated requests to `<base>/auth/env-start`. |
| `HUMR_JWKS_URL` | `https://humanityrules.io/.well-known/jwks.json` | Central JWKS endpoint for verifying session JWTs. |
| `HUMR_PDP_URL` | `https://humanityrules.io/api/pdp/evaluate` | Central authorization endpoint. |
| `HUMR_ENV_BEARER` | 64 random chars | Environment bearer token. From Secrets Manager. Used by any env component calling the HUMR control plane. |
| `HUMR_UPSTREAM_HOST` | `127.0.0.1` | The app container. |
| `HUMR_UPSTREAM_PORT` | `8787` | The app container's port. |
| `HUMR_LISTEN_PORT` | `8443` | Port the policy proxy listens on. ALB routes here. |

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

The policy-proxy image is published to ECR per environment — `doh/{env_slug}/policy-proxy:{version}` — from the HUMR deploy pipeline on first policy-proxy deploy. See `humanityrules_app/services/infra_customer/` for the push plumbing.
