# Personal Assistant Deployment — Current State Report

## What this feature is

DOH deploys **Hermes Agent** — a governed AI personal assistant — into a customer's AWS VPC as a Fargate app, via the same one-click template-deploy flow used for OpenClaw. Employees get a real agent (tool execution, persistent memory, skills) reachable through WebUI and Slack, behind per-user SSO + ABAC. Companies keep central governance: approved model providers, per-deployment IAM, per-agent EFS isolation, secrets managed centrally, and runtime access gated by the same ABAC engine used everywhere else in DOH.

## Core pieces in place

- **Hermes Agent AppTemplate** — full template repo under `template_repos/hermes_agent/`.
- **Deploy flow** — template picker → deploy form with grouped, user-editable runtime variables (Main LLM, Auxiliary LLM, Slack). Template authors decide what's operator-tunable per deploy vs hidden.
- **Multi-channel** — WebUI + Slack gateway run side-by-side when Slack tokens are present; WebUI-only otherwise. Proactive messages land in a configurable Slack home channel.
- **Persistent state on EFS** — per-app access point gives chroot-like path + UID isolation so agents can't see each other's files. Agent memory, skills, and workspace output survive task restarts.
- **Bedrock governance path** — "nothing leaves our VPC" supported via Bedrock provider. Gaps in upstream Hermes are worked around by a runtime patch system that applies unified diffs idempotently on every boot, so the fixes naturally become no-ops if upstream lands them.
- **Policy proxy for per-user SSO + ABAC** — every Hermes app ships with a two-container task where a policy proxy owns the public port and gates every request. Verifies a session JWT, calls DOH's central PDP for an ABAC decision, injects `X-Auth-*` headers upstream, fails closed on PDP outage. In-memory decision cache keyed on user.
- **Central auth endpoint per env** — single Okta redirect URI per env; one login covers every policy-proxy'd app in the env via a cookie on the parent env domain.
- **ABAC owner primitive** — `$resource.owner` / `$identity.<key>` condition form lets a single global policy express "owner can use their own PA" across N apps, instead of one policy per user. `username` is locked post-creation because it's the ABAC anchor.
- **OIDC / Okta login** — orgs can be flipped to OIDC; WorkOS and OIDC can coexist on the same user.
- **Per-env trust anchors** — three per-env Secrets Manager entries back the whole system: shared API keys + policy proxy bearer token, JWT signing keypair, Okta OIDC config. Keeps per-app secret sprawl down and scopes the trust boundary to the env's VPC.
- **Hermes-specific operator tooling** — `policy_proxy_mint_cookie` (bypass Okta for manual UI debugging), `policy_proxy_simulate` (local PDP round-trip), `policy_proxy_e2e_test` (hermetic real-AWS orchestrator against a mock PDP), `setup_oidc_org`.

## Known constraints / open surface

- Sidecar v1: one Okta app per org, single-env orgs only. Multi-env orgs need a per-env OIDC config — deferred.
- `HERMES_WEBUI_PASSWORD` is no longer set on the Personal template — the WebUI's built-in password auth is disabled and the policy proxy is the sole gate. Paired with `HERMES_WEBUI_HOST=127.0.0.1` so the WebUI binds loopback-only and isn't reachable from the VPC. (The Slack template still uses the password — it isn't behind the policy proxy.)
- Hermes WebUI `X-Auth-*` header consumption not yet spiked — policy proxy injects them but upstream integration is untested.
- Aux LLM assumes one shared provider/model across all 8 auxiliary slots (simple knob; not per-task tunable yet).
- WebUI base image bumps are manual per customer app — no cross-deployment rollout mechanism yet.

## Where the governance story stands

Runtime access control for Hermes is now real: per-user SSO via Okta, per-request ABAC decisions via DOH's central engine, fail-closed policy proxy, per-env trust boundary. ABAC / approval workflows / audit trails are inherited from DOH's existing pipeline — the Hermes template rides that infrastructure rather than adding a parallel path. The governance pieces specific to this feature: policy proxy-enforced owner-only access, model-provider pinning (Bedrock), per-agent EFS isolation, and the user-editable-vs-hidden split on runtime variables.
