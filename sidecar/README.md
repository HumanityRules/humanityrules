# DOH Sidecar Proxy

A tiny reverse proxy that sits in front of apps deployed by DevOps Hero and enforces SSO + ABAC at the edge. See `docs/sidecar_proxy_design.md` for the full design.

## What it does, per request

1. Reads the `doh_session` cookie.
2. If missing or invalid, 302s to `https://auth.<env-domain>/start?rd=<current-url>` for the OAuth dance.
3. Verifies the JWT against the env's public key (fetched from `/.well-known/jwks.json` at startup).
4. POSTs to DOH's PDP endpoint with `{app_id, oidc_sub, username, path}` and `Authorization: Bearer <DOH_SIDECAR_TOKEN>`.
5. On `allow`, proxies to the app container on localhost, injecting trusted identity headers.
6. On `deny`, returns a 403 with a short message.

## Config (all env vars; no defaults)

| Var | Example | Purpose |
| --- | --- | --- |
| `DOH_APP_ID` | `vmendi-hermes` | App slug, sent in the PDP request. |
| `DOH_ENV_SLUG` | `ch-sandbox` | For log lines only. |
| `DOH_ENV_DOMAIN` | `ch-sandbox.chsandbox.com` | Parent domain the session cookie is scoped to. |
| `DOH_AUTH_BASE_URL` | `https://auth.ch-sandbox.chsandbox.com` | Where to redirect for login. |
| `DOH_JWKS_URL` | `https://auth.ch-sandbox.chsandbox.com/.well-known/jwks.json` | JWT verification keys. |
| `DOH_PDP_URL` | `https://devopshero.ai/api/pdp/evaluate` | Central authorization endpoint. |
| `DOH_SIDECAR_TOKEN` | 64 random chars | Bearer token for the PDP call. From Secrets Manager. |
| `DOH_UPSTREAM_HOST` | `127.0.0.1` | The app container. |
| `DOH_UPSTREAM_PORT` | `8787` | The app container's port. |
| `DOH_LISTEN_PORT` | `8443` | Port the sidecar listens on. ALB routes here. |

## Local development

```bash
cd sidecar
uv run python -m sidecar.main  # relies on the env vars above
```

For unit tests:

```bash
cd sidecar
uv run pytest
```

## Publishing

The sidecar image is published to ECR per environment — `doh/{env_slug}/sidecar:{version}` — from the DOH deploy pipeline on first sidecar-enabled deploy. See `devopshero_app/services/infra_customer/` for the push plumbing.
