# Hermes deployment security review — report

**Date:** 2026-07-30  
**Scope:** HumR-owned code and architecture around Hermes Agent deployment. Upstream Hermes (`vendor/`) out of scope.  
**Detail threads:** [`01`](01_tls_credential_broker.md) · [`02`](02_mcp_merge.md) · [`03`](03_sso_policy_proxy_abac.md) · [`04`](04_customer_deploy_infra.md) · [`05`](05_in_container_sandbox.md) · [`06`](06_iam_permissions_approvals.md) · [`07`](07_webapps_public_access.md)

## Summary

Custody and gate design are mostly right in intent: refresh tokens stay on CP, policy proxy fail-closes, PA owner ABAC is real, Merge key never enters the customer container, per-app task roles/EFS are scoped at deploy. The problems that matter before customers are **escape hatches that collapse those boundaries** — especially sandbox→IMDS via the broker CONNECT tunnel, IAM Apply that does not freeze what was approved, a 30-day irrevocable env session, and `AdministratorAccess` as the customer install role. Several High findings are the same bug in different clothes: **one env bearer + client-supplied identity + unauthenticated loopback `:9951`**.

## Decide first

Ordered by “if this is real, fix or accept before GA.” One line each; open the area file for evidence.

1. **Sandbox can pull ECS task-role creds via broker CONNECT → IMDS** — opaque tunnel has no destination filter; nono’s IMDS block is bypassed. ([01](01_tls_credential_broker.md), [05](05_in_container_sandbox.md))
2. **Customer install role is `AdministratorAccess`** — any HumR STS session with the ExternalId is full account compromise. ([04](04_customer_deploy_infra.md))
3. **IAM “approved” statements stay mutable; Apply reads live JSON** — HTML editor has no draft gate; race after approve. ([06](06_iam_permissions_approvals.md))
4. **HumrClient lets payload override `owner_username` / `app_slug`** — cross-app permissions (and same pattern risk elsewhere) inside one env. ([06](06_iam_permissions_approvals.md))
5. **Env session JWT is 30 days with no revocation** — design said 1h; IdP logout does not kill env access. ([03](03_sso_policy_proxy_abac.md))
6. **`WEBAPP_PORT` can retarget Caddy outside 4000–4019** — WebUI / gateway / broker / process-compose; catastrophic with a public grant. ([07](07_webapps_public_access.md), [05](05_in_container_sandbox.md))
7. **Install Create callback rebinds any matching ExternalId** — no PENDING/ARN checks; leaked connect URL → attacker account hijack. ([04](04_customer_deploy_infra.md))
8. **Agent can Apply IAM (and drive integration lifecycle) on `:9951` with no extra auth** — “human-only Apply” is tool-layer fiction. ([06](06_iam_permissions_approvals.md), [02](02_mcp_merge.md), [01](01_tls_credential_broker.md))

## Themes (judgment, not tickets)

1. **Env = one trust domain.** Shared `HUMR_ENV_BEARER`, Merge identity from headers, PDP `app_id` from the proxy — compromise of one app is compromise of the env’s CP surface. Per-app bearers vs document-and-accept.
2. **Sandbox peers trust each other.** Loopback signer, broker, MCP, process-compose, gateway key are open to the agent UID. Public Web Apps inherit that. Decide whether “agent is TCB” is the GA story.
3. **ABAC anchors are editable.** `owner` / `app-type` tags and `username` identity attributes can be changed by admins; PA takeover without redeploy. ([03](03_sso_policy_proxy_abac.md))
4. **Shared sandbox is soft multi-tenancy.** Flat VPC SG, packed EC2, `SYS_ADMIN`, shared SSO zone resolution. Fine for HumR-ops sandbox if stated; not a customer isolation claim. ([04](04_customer_deploy_infra.md), [03](03_sso_policy_proxy_abac.md))
5. **Environment teardown leaks secrets.** SM secrets survive env teardown. (App *removal* now purges everything unconditionally; `IntegrationUserCredential` survives it as accepted per-user retention.) ([04](04_customer_deploy_infra.md), [01](01_tls_credential_broker.md))
6. **Direct `:443` egress + unfiltered aws_signer** — broker bypass and STS-shaped credential minting into the sandbox if IAM ever allows it. ([05](05_in_container_sandbox.md))

## What looks sound

1. Refresh tokens / OAuth client secrets / Merge tenant key stay off the sandbox (and Merge key off the customer account).
2. Policy proxy: RS256, identity-header strip, fail-closed PDP, PA skips open-access default policy, public grants org-admin-only.
3. Deploy isolation primitives: ExternalId trust, hashed env bearer in CP, dashless host labels, per-app task-role + EFS AP conditions, content-addressed images.
4. Permissions Apply has real ABAC + sandbox platform-owner gate and tenant-consistency before `put_role_policy` — integrity of *what* is applied is the hole, not the gate existing.

## Suggested triage order

1. CONNECT deny-list (IMDS/link-local) + confirm IMDSv2 hop limit — closes the clearest custody break.  
2. Freeze IAM statements on approve + force broker identity last — closes two Critical Apply paths.  
3. Clamp `WEBAPP_PORT` / route targets to the pool — especially before marketing public webapps.  
4. Session TTL / revocation — product call, but 30d + no kill switch is hard to defend to enterprise.  
5. Install role least-privilege + Create-callback PENDING/ARN checks — customer-account blast radius.  
6. Everything else from the area files as capacity allows.
