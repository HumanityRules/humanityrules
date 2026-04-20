# Sidecar Auth Lambda

One per DOH environment. Handles the Okta OAuth flow and mints `doh_session` JWTs that sidecars verify per request. Fronted by the env ALB via a host-based listener rule on `auth.<env-domain>`.

## Routes

- `GET /start?rd=<url>` — begin the OAuth dance. Carries `rd` in the OAuth `state` so Okta's single registered `redirect_uri` works for any in-env destination.
- `GET /callback?code=...&state=...` — exchange code with Okta, fetch userinfo, mint JWT, set cookie, 302 to `rd`.
- `GET /.well-known/jwks.json` — serve the env's public key for sidecar verification.

## Runtime model

Triggered by ALB (not API Gateway). The Lambda is configured with a single Secrets Manager ARN passed as an env var:

- `DOH_SIDECAR_AUTH_CONFIG_SECRET_ARN` — JSON blob with both the OIDC config and the env's JWT signing keypair:
  `{"oidc_config": {"issuer_url", "client_id", "client_secret"}, "jwt_key": {"private_pem", "public_pem", "kid"}}`.

The secret is provisioned at env setup time (see workstream (g)).

## Dependencies

`authlib` (for the OAuth dance), `pyjwt[crypto]`, `cryptography`. Packaged as a CDK `lambda.Code.from_asset` with dependencies bundled. See the companion CDK construct.

## Local testing

Unit tests invoke `handler(event, context)` directly with synthesized ALB events. The secrets manager client is monkey-patched to return in-test values.

```bash
cd lambdas/sidecar_auth
uv run pytest
```
