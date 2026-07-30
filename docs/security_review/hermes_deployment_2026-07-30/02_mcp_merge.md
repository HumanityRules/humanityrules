# Area 2: MCP Aggregator & Merge

## Scope

Sandbox-facing MCP transport (port 9952), direct-MCP OAuth (PostHog enabled; Notion DCR present but disabled), and Merge.dev passthrough where the Merge tenant key stays on HUMR and tools are progressive-disclosed to the agent.

**In scope (HumR-owned only):**

- Runtime: `template_repos/hermes_agent/humr_runtime/integrations/` — `mcp_aggregator.py`, `mcp_merge_backend.py`, `mcp_top_level_tools.py`, `connectors/`, mount points in `humr_broker.py` / `control_api.py`
- CP: `humanityrules_app/views/integrations/merge_handler.py`, routes under `api/integrations/merge/*`
- Docs: `docs/integrations/mcp_aggregator_design.md`, `docs/integrations/merge_integration_design.md`
- WebUI: `template_repos/hermes_agent/webui-extension/humr-integrations-merge.js` (+ OAuth start wiring in `humr-integrations-runtime.js`)
- Related nono / Caddy assumptions needed to judge loopback trust: `hermes-nono-profile.json`, `http_router/Caddyfile`

**Out of scope:** TLS MITM injection details (except shared secrets/auth assumptions), SSO/ABAC policy design, ECS infra, IAM editor, webapps product surface beyond same-origin CSRF implications for the broker.

## Summary

The custody model is sound in the ways that matter most: the Merge tenant API key never enters the customer container; direct-MCP refresh tokens stay on EBS outside the nono sandbox; sandbox→9952 is loopback-bound; CP constructs Merge URLs from authenticated state rather than forwarding arbitrary Merge paths; progressive disclosure keeps ~1500 tools out of the prompt while still gating calls on connector connection status.

The main residual risks are **authorization gaps around who the env bearer is allowed to act as**, **unauthenticated control-plane mutations reachable from the sandbox (and same-origin agent-built UI)**, and **prompt-injection amplification** via passthrough tool results / Magic Links / advisory-only `mutates` flags. No Critical cross-tenant Merge-key leak was found in HumR-owned code.

## Findings

### [High] Env bearer + client-supplied identity allows cross-app Merge confused deputy

- **Location:** `humanityrules_app/views/integrations/merge_handler.py` `_resolve_caller` (lines 52–106); callers such as `integrations_merge_mcp` (408–443); broker injection in `mcp_merge_backend.py` (84–88, 315–325). Contrast with stricter `broker_request_context.resolve_owned_app_slug` (filters `environment=environment`, lines 60–79).
- **Issue:** `HUMR_ENV_BEARER` is **per-environment** (shared Secrets Manager field injected into every app that needs the bearer — `deploy_app.py` ~404–421). Merge identity (`app_slug` / `owner_username`) is taken from request headers or body and only checked for: (1) user in org, (2) app slug exists in org, (3) `owner` ResourceTag matches. Unlike other broker endpoints, Merge does **not** require `app.environment == bearer.environment`. Any holder of an env bearer (compromised broker, `ecs:ExecuteCommand` reading environ, stolen shared secret) can invoke Magic Link minting, disconnect, and MCP tool relay as **any** `(owner, app_slug)` pair in the organization that has a matching owner tag — including apps in other environments of the same org.
- **Impact:** Cross-assistant abuse inside a customer org: Agent A’s compromised container operates Agent B’s Slack/GitHub/etc. grants via Merge Registered User `humr_{pk}_{slug}`. This is weaker than the design claim that “the broker has no way to forge a different identity” (`merge_integration_design.md` ~82) and weaker than the documented env-compromise bound for TLS-intercept tokens (`integrations_broker_design.md` ~223–224).
- **Next:** Bind Merge identity to the bearer more tightly: require `App.environment_id == environment.id`, and prefer deriving `app_slug`/`owner` from deploy-time attested claims (or a per-app bearer) rather than trusting client-supplied headers. Align `_resolve_caller` with `resolve_owned_app_slug`.

### [High] Sandbox can mutate integration control APIs on :9951 with no extra auth

- **Location:** `hermes-nono-profile.json` `network.open_port` includes `9951` and `9952` (lines 102–115); `control_api.py` mounts Merge + MCP OAuth routes with no request authentication (lines 165–181); `mcp_merge_backend.py` `handle_link_token` / `handle_disconnect` (273–300); `mcp_aggregator.py` `handle_oauth_start` / `handle_disconnect` (317–325, 326–377).
- **Issue:** Design separates “9952 = sandbox MCP” from “9951 = browser management via Caddy,” but nono deliberately grants sandbox TCP to **both**. Control routes have no CSRF token, no shared secret, and no browser-vs-agent distinction. A prompt-injected agent (or any sandbox process, including agent-built servers on listen ports 4000–4019) can `curl` `127.0.0.1:9951` to mint Merge Magic Links, disconnect connectors, start/clear DCR OAuth state, and refresh catalogs. Docs already treat malicious sandbox skills as able to call tools for the current user (`integrations_broker_design.md` ~224–226); this extends that to **lifecycle** operations that the UI presents as human-driven.
- **Impact:** Prompt-injection → silent disconnect of integrations; Magic Link URLs emitted into chat/logs for phishing or unwanted connector binding; OAuth start with attacker-chosen `return_to` / `origin` (see below). Amplifies agent compromise beyond “call already-connected tools.”
- **Next:** Split ports or auth: keep 9952 for MCP tool calls only; require a broker-local capability token or Unix-socket ACL for lifecycle routes; or remove 9951 from sandbox `open_port` and force agent lifecycle actions through an explicit, audited MCP meta-tool with policy.

### [High] Same-origin Widgets/Web Apps can CSRF broker lifecycle endpoints

- **Location:** `http_router/Caddyfile` routes `/__humr_broker/*` on the public Hermes hostname (lines 11–17); `docs/app_workloads_design.md` states Widget JS is same-origin with WebUI by design; WebUI Merge client `humr-integrations-merge.js` (11–18, 84–90) posts to `/__humr_broker/integrations/merge/*` with no CSRF header; `control_api.py` notes WebUI CSRF does not apply (design doc ~72).
- **Issue:** Browser session (policy-proxy SSO cookie) + same-origin agent-authored Widget/Web App JS can issue `fetch('/__humr_broker/integrations/merge/disconnect', …)` or `link-token` without user gesture beyond loading the page. Broker endpoints are cookie-authenticated only by network reachability through the authenticated app hostname, not by an anti-CSRF token.
- **Impact:** Malicious or prompt-injected Widget code running in the owner’s browser can disconnect integrations or mint Magic Links while the owner is logged in — confused deputy of the browser session against the broker.
- **Next:** Add CSRF (or double-submit / Fetch metadata checks) on mutating `/__humr_broker/integrations/*` routes; treat agent-built same-origin UI as hostile to lifecycle APIs unless explicitly gated.

### [Medium] OAuth `return_to` / `origin` are attacker-influenced open redirects and redirect_uri inputs

- **Location:** `mcp_aggregator.py` `handle_oauth_start` (332–338, 364), `handle_oauth_callback` (420–421); WebUI builds query in `humr-integrations-runtime.js` (159–167).
- **Issue:** `return_to` is stored in `_pending_oauth` and used verbatim in `RedirectResponse` after token exchange — no allowlist to the app’s public origin. `origin` becomes the OAuth `redirect_uri` prefix and can overwrite `self._public_base_url` on first connect. Combined with sandbox/CSRF reachability of oauth/start, an attacker can (1) open-redirect the user after a successful connect for phishing, and (2) attempt DCR registration against a hostile `redirect_uri`. PKCE (`code_verifier` stays on the broker) blocks straightforward auth-code theft via a hostile redirect_uri **if** the AS enforces S256 correctly; it does not fix open redirects on `return_to`.
- **Impact:** Post-OAuth phishing; polluted DCR client registrations; reliance on third-party PKCE enforcement for token safety when `origin` is attacker-controlled.
- **Next:** Allowlist `return_to` and `origin` to `HUMR_APP_PUBLIC_URL` / `HUMR_PUBLIC_HOSTNAME` only; ignore client `origin` when public base URL is already configured; reject absolute external URLs.

### [Medium] Direct-MCP disconnect does not revoke upstream tokens

- **Location:** `mcp_aggregator.py` `handle_disconnect` (317–324); design intends revocation in `mcp_aggregator_design.md` (~94–99).
- **Issue:** Disconnect only `clear_token()` / `clear_client()` on local disk. No call to the provider’s revocation endpoint; refresh tokens remain valid at PostHog (or future DCR providers) until they expire or are revoked elsewhere.
- **Impact:** “Disconnect” in the UI is local custody drop only. Anyone who previously copied `token.json` (EBS snapshot, compromised parent process) or a still-valid refresh token continues to access the user’s PostHog workspace.
- **Next:** Implement RFC 7009 revocation when metadata advertises `revocation_endpoint`; on failure, still delete local state but surface “revoke failed” to the user/ops.

### [Medium] `mutates` is advisory only — no human confirmation gate on tool calls

- **Location:** `mcp_top_level_tools.py` `integrations_call_tool` (511–539); heuristics in `connectors/_common.py` (18–30) and `mcp_merge_backend.py` (49–59). Comment in `_common.py` claims confirmation is “much cheaper than silently mutating,” but no runtime enforces it.
- **Issue:** `integrations_call_tool` checks catalog presence and `connector_status == "connected"`, then forwards args upstream with no schema validation beyond FastMCP/upstream and no branch on `entry.mutates`. Classification mistakes (unknown verbs default to mutates=True for search UX, but the LLM can ignore the flag) do not block execution.
- **Impact:** Prompt injection can drive destructive Merge/PostHog actions (delete, send, update) in one tool hop once the connector is connected — the progressive-disclosure layer does not add an authorization checkpoint.
- **Next:** Product decision: enforce confirmation (or ABAC) for `mutates=True` before `backend.call`; or document explicitly that connected connector ⇒ full agent autonomy and compensate with connector allowlists / per-org Tool Packs.

### [Medium] Merge / upstream tool content is a prompt-injection channel (including Magic Links)

- **Location:** `mcp_merge_backend.py` `call` (173–183) returns Merge content verbatim; `merge_integration_design.md` Path α (~122–126) documents `authenticate_meta` with `magic_link_url` and a message “written for the model to relay to the user verbatim”; catalog descriptions come from upstream `tools/list` (`list_tool_catalog` 133–156).
- **Issue:** Tool descriptions, error payloads, and reauth messages are untrusted third-party text injected into the agent context. Magic Links are attacker-valuable if the agent can be induced to exfiltrate them or if a compromised upstream/Merge path plants instructions.
- **Impact:** Classic indirect prompt injection: “ignore previous instructions…,” phishing the user with a crafted Magic Link narrative, or steering the agent toward destructive follow-up `integrations_call_tool` calls.
- **Next:** Treat upstream text as untrusted: wrap/relabel in a fixed envelope; never instruct the model to relay verbatim; optionally require WebUI-originated connect for Magic Links rather than chat-pasted URLs for high-risk connectors.

### [Medium] PostHog OAuth default scope is maximally broad; agent can widen session surface

- **Location:** `connectors/posthog.py` `_DEFAULT_SCOPE` (56–80); `posthog-set-config` meta-tool (207–265) callable via `integrations_call_tool` when connected.
- **Issue:** Consent requests nearly every PostHog read/write scope category. Separately, once connected, the LLM can call `posthog-set-config` to clear feature filters (full ~350-tool firehose) or retarget `organization_id` / `project_id` without a human step.
- **Impact:** Over-privilege at consent time; post-connect privilege expansion and cross-project access within the authorized PostHog org, driven by prompt injection.
- **Next:** Narrow default scopes to product-needed minimums; require WebUI confirmation for `posthog-set-config` changes (especially clearing filters or changing project/org).

### [Medium] Long-lived Merge MCP streams can pin CP workers

- **Location:** `merge_handler.py` `MERGE_MCP_READ_TIMEOUT_SECONDS = 600` (36–37); `integrations_merge_mcp` uses sync `httpx.Client` + `StreamingHttpResponse` (460–493).
- **Issue:** Each MCP session may hold a Django worker/thread for up to 10 minutes while streaming. Many Hermes apps reconnecting or leaving SSE GETs open can exhaust CP capacity. No per-org concurrency limit is evident on this path.
- **Impact:** Availability DoS against the control plane’s Merge relay (and potentially other Django traffic on the same workers), triggered from customer envs that already hold a valid bearer.
- **Next:** Move MCP relay to async/ASGI or a dedicated service; cap concurrent streams per env/org; consider shorter idle timeouts for GET/SSE.

### [Low] Direct-MCP OAuth tokens written without restrictive file modes

- **Location:** `mcp_aggregator.py` `_OAuthState.save_client` / `save_token` (104–115) use `Path.write_text` with no `chmod 0o600`. Contrast TLS leaf handling in `tls_certificate_authority.py` (chmod 0o600 / private dir 0o700). Persistent path: `/hermes-persistent-root/mcp-aggregator/<provider>/token.json` (`humr_broker.py` 68, 153–156).
- **Issue:** Tokens land with default umask permissions. Sandbox nono profile does not grant `/hermes-persistent-root` (good), but other parent-container UIDs or volume clones may still read world/group-readable token files.
- **Impact:** Local privilege expansion inside the task or backup/volume leakage exposes refresh tokens for PostHog (and future DCR providers).
- **Next:** `os.open`/`chmod 0o600` before write; ensure directory mode `0o700`; document EBS snapshot access as equivalent to token custody.

### [Low] In-memory OAuth `state` / PKCE verifiers never expire

- **Location:** `mcp_aggregator.py` `_pending_oauth` (151, 364, 392–395).
- **Issue:** Pending entries are removed only on successful/failed callback pop. Abandoned starts accumulate until process restart; there is no TTL.
- **Impact:** Mild memory growth; larger `_pending_oauth` increases the window for state guessing (still needs 256-bit `state`, so brute force is impractical).
- **Next:** TTL + max size eviction (e.g. 10–15 minutes).

### [Low] Catalog `tool_id` collisions can silently drop or overwrite tools

- **Location:** `mcp_top_level_tools.py` `_reload_locked` (228–239) overwrites on collision; `_replace_backend_slice` (184–191) keeps existing and drops new.
- **Issue:** Flat catalog keys by upstream `tool_id` with no backend namespace. Merge `connector__tool` vs a future native tool with the same name can hide tools or route calls to the wrong backend depending on reload path.
- **Impact:** Wrong-backend invocation or missing tools; confusing security reviews of what the agent can call.
- **Next:** Namespace catalog keys as `{backend}:{tool_id}` internally while preserving display ids carefully.

### [Info] Orphaned Merge Registered Users and local MCP tokens on app destroy

- **Location:** Documented in `merge_integration_design.md` (~164–165); local tokens under `/hermes-persistent-root/mcp-aggregator/`; related CP credential cleanup gaps in `docs/app_removal_data_cleanup_audit.md`.
- **Issue:** App teardown does not delete Merge Registered Users or guarantee purge of direct-MCP token files beyond EFS wipe flags.
- **Impact:** Stale grants at Merge; possible “ghost reconnect” if slug reused with same owner (Merge identity is `humr_{pk}_{slug}`).
- **Next:** Teardown hook: Merge disconnect-all / delete registered user; ensure persistent-root wipe covers `mcp-aggregator/`.

### [Info] Single global Merge Tool Pack for all customers

- **Location:** `merge_integration_design.md` (~142–144); `settings.MERGE_TOOL_PACK_ID` in `merge_handler.py` (117–122, 443).
- **Issue:** Every org shares one Tool Pack; per-org disable of connectors is not available. Cross-tenant isolation relies on `origin_user_id`, not pack separation.
- **Impact:** Product/security policy limitation (CIO cannot disable a connector org-wide) rather than a direct cross-tenant data break — still widens blast radius of a bad connector in the pack.
- **Next:** Per-org Tool Pack when customers need deny-lists; keep constructing pack id server-side.

### [Info] MCP :9952 has no application-layer auth (by design)

- **Location:** `mcp_aggregator.py` `serve` binds `127.0.0.1` (231–237); `config.yaml.template` points Hermes at `http://127.0.0.1:9952/mcp`; nono `open_port` 9952.
- **Issue:** Any sandbox process that can speak MCP can use the owner’s connected integrations. This matches the stated trust model (sandbox agent ≡ user tool principal) but means progressive disclosure is a UX/context optimization, not a privilege boundary between sandbox workloads.
- **Impact:** Agent-built webapps/skills inherit full connector power for this app.
- **Next:** Accept and document; or introduce per-workload MCP credentials if untrusted sandbox code becomes a first-class threat.

## Sound design notes

1. **Merge tenant key custody.** `MERGE_AGENT_HANDLER_API_KEY` stays on HUMR (`merge_handler.py` `_api_key_or_500`); customer brokers only present `HUMR_ENV_BEARER`. This correctly addresses the enumerated risk that a customer admin can read container environ.
2. **Narrow Merge HTTP surface.** Endpoints are purpose-built (`ensure-registered-user`, `link-token`, `connectors`, `connector-status`, `disconnect`, `mcp`) — not a generic Merge path proxy. Path IDs (`tool_pack_id`, `registered_user_id`) are resolved server-side (`integrations_merge_mcp` ~416–443).
3. **Owner tag check.** Even with client-supplied identity, non-owners cannot attach to an app (`merge_handler.py` 96–104; covered by `test_integrations_merge_handler.py`).
4. **Direct-MCP tokens outside sandbox.** OAuth material under `/hermes-persistent-root/mcp-aggregator/` with nono not granting that path; sandbox allow-list omits `HUMR_ENV_BEARER`.
5. **Loopback binding.** Aggregator and control API listen on `127.0.0.1` only; not published on the task ENI.
6. **PKCE + one-time `state`.** OAuth start uses S256 and pops state on callback (`mcp_aggregator.py` 361–397).
7. **Progressive disclosure + `authenticated_only=true`.** Four top-level tools avoid dumping ~1500 schemas into the prompt; Merge MCP URL requests authenticated connectors only (`merge_handler.py` 438–443), shrinking both context and default callable set.
8. **Native-over-Merge exclusion.** DCR slugs plus `github`/`slack`/`x` are excluded from Merge catalog (`mcp_aggregator.py` 168–174) to avoid duplicate/confused auth planes.
9. **Connection gate on call.** `integrations_call_tool` refuses non-connected connectors with structured `not_connected` (`mcp_top_level_tools.py` 528–534).

## Open questions / needs product judgment

1. **Is “connected connector ⇒ full agent autonomy” accepted?** If yes, document it as the security boundary and invest in org-level connector allowlists / Tool Packs. If no, `mutates` confirmation or PDP checks are required before High findings on tool invocation can close.
2. **Should sandbox code be allowed to manage integration lifecycle?** Permissions phase 2 intentionally uses 9951 from the agent; Merge/OAuth disconnect and Magic Link minting may need a different answer than “read IAM.”
3. **Per-app vs per-env bearer.** Fixing cross-app Merge confused deputy may force a credential model change (per-app bearer or signed app attestation), not just an extra queryset filter.
4. **Widget/Web App trust tier.** Same-origin agent UI vs broker CSRF protection — product may need a “trusted panel only” rule for connect/disconnect.
5. **Merge Magic Links in chat (Path α).** Keep as-is for UX, or require Integrations-panel completion for high-risk connectors?
6. **PostHog scope minimization.** How much workspace write access is actually required for the PA use case?
)
