# GitLab

`https://gitlab.com/api/v4/mcp` and `https://gitlab.com/api/v4/orbit/mcp`

## DCR status

- AS metadata `https://gitlab.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://gitlab.com/oauth/register`. DCR live-tested.
- **`scopes_supported`** (26): `api`, `read_api`, `read_user`, `create_runner`, `manage_runner`, `k8s_proxy`, `self_rotate`, **`mcp`**, **`mcp_orbit`**, `read_repository`, `write_repository`, `read_registry`, `write_registry`, `read_virtual_registry`, `write_virtual_registry`, **`read_observability`**, `write_observability`, `ai_features`, `sudo`, `admin_mode`, `read_service_ping`, `openid`, `profile`, `email`, `ai_workflows`, `user:*`.
- `token_endpoint_auth_methods`: `client_secret_basic`, `client_secret_post` (no `none` — confidential clients only at GitLab.com AS).
- **Two parallel MCP endpoints** in one product: `/mcp` (general) and `/orbit/mcp` (Duo Orbit agent surface).

`scopes_supported` on the resource doc lists `["mcp", "mcp_orbit"]` — those are the MCP entry-gate scopes; clients also need domain scopes (`api`, `read_repository`, etc.) layered on top to actually call the underlying tools. **Dual-axis scoping.**

## Did Merge do a good job?

**Yes for breadth.** Merge ships **96 tools** for GitLab — the highest count in the entire candidate list. GitLab's first-party MCP tool list is not fully verified (docs.gitlab.com was inaccessible during this audit), but the surface is presumably wide given the underlying API.

## Would direct integration be advantageous?

**Yes, with serious caveats.** Direct gets us:

1. **OAuth via DCR** instead of Merge's `OAuth or private token`.
2. **Dual MCP endpoint choice** — agents can target the Duo Orbit agentic surface separately from the general API surface.
3. **Fine-grained scope catalog** — closest to Ramp and PostHog in shape.
4. **`read_observability` scope** for GitLab's observability surface (logs, traces, metrics) that Merge probably doesn't cover.

Caveats:
- **Self-managed GitLab instances** are common in enterprise. The aggregator must accept a configurable instance URL per connection and re-discover `/.well-known/oauth-authorization-server` on that instance. DCR may be disabled by self-managed admins — needs runtime detection.
- **Dual-axis scoping** (entry-gate `mcp` scope + domain scope like `read_repository`) means we have to compose `default_scope` carefully.

## Mechanisms beyond Notion

- **Configurable instance URL per connection.** Aggregator must accept the customer's GitLab host (gitlab.com or self-managed) and re-discover OAuth metadata. Same shape as Sentry's self-hosted support.
- **Dual MCP endpoints.** `/mcp` vs `/orbit/mcp`. Could be modeled as two separate connectors, or as a per-session config knob.
- **26 advertised scopes** with mandatory `mcp` or `mcp_orbit` entry-gate.
- No `?features=`/`?tools=` URL params.
- **OIDC claims** carry rich GitLab-specific claims (`project_path`, `ci_config_ref_uri`, `ref_path`, group ownership) — not relevant unless we want to reason about CI runtime.

## Effort

**High.** Plan ~1 week:

- `connectors/gitlab.py` with `default_scope = "mcp api read_repository read_observability ..."` covering both entry-gate and domain scopes for the tools we want.
- Per-connection instance URL handling — same pattern as Sentry self-hosted.
- Decide: two connectors (`gitlab` + `gitlab-orbit`) or one connector with an endpoint toggle.
- Confirm DCR is enabled on the customer's instance before authorizing; fall back to "needs admin to enable DCR" error otherwise.

This is **the most justified direct-integration candidate after Datadog and Sentry**, and the heaviest of the three.

## Sources

- AS metadata: https://gitlab.com/.well-known/oauth-authorization-server
- Resource metadata: https://gitlab.com/.well-known/oauth-protected-resource
- Docs (couldn't fetch during audit): https://docs.gitlab.com/api/mcp/
