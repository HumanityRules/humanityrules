# Sentry

`https://mcp.sentry.dev/mcp` (legacy `/sse` deprecated)

## DCR status

- AS metadata `https://mcp.sentry.dev/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.sentry.dev/oauth/register`. DCR live-tested.
- **`scopes_supported = ["org:read", "project:write", "team:write", "event:write"]`** — explicit, fine-grained.

## Did Merge do a good job?

**Yes for breadth, no for depth.** Merge ships **41 tools**. Sentry's first-party MCP caps tool count at ≤25 by design ("AI agents have limited tool slots"). The tool surface is curated:

- Discovery: `whoami`, `find_organizations`, `find_projects`, `find_teams`, `find_releases`, `find_dsns`
- CRUD: `create_project`, `create_team`, `create_dsn`, `update_project`, `update_issue`
- Issue investigation: `get_issue_details`, `get_issue_tag_values`, `search_issues`, `search_issue_events`, `search_events`
- Performance/profiling/replay: `get_profile`, `get_profile_details`, `get_trace_details`, `get_replay_details`, `get_event_attachment`
- Docs/resources: `search_docs`, `get_doc`, `get_sentry_resource`
- Seer/agent skill: `analyze_issue_with_seer`, `use_sentry` (an embedded-agent meta-tool that delegates to ~18 internal tools via in-memory MCP transport)

Merge's 41 tools spread thinner: more event/issue CRUD, less of Sentry's investigation depth. **`use_sentry` and `analyze_issue_with_seer` are unique** — embedded-agent patterns Merge cannot replicate.

## Would direct integration be advantageous?

**Yes, clearly.** Sentry's MCP is the most sophisticated of any vendor in this audit. Reasons to go direct:

1. **Layered OAuth** with two distinct tokens (upstream Sentry stays server-side; downstream MCP token carries skill grants). Refresh cannot widen access — fail-closed semantics. Cleaner than Merge's relay.
2. **Path-based session scoping** (`/mcp/:org/:project`) means we can hand the agent a token bound to one project, narrowing the cognitive load.
3. **Skills system** lets us turn off Seer or other gated capabilities at session start.
4. **Embedded agent tools** (`search_events`, `search_issues`, `use_sentry`) run a GPT-5 sub-agent against ~18 internal tools — not exposed by Merge.

## Mechanisms beyond Notion

This is **the most novel vendor in the audit**, more complex than PostHog:

- **Per-resource scopes:** `org:read`, `project:write`, `team:write`, `event:write`. Fixed at four — not as wide as PostHog's 171, but explicit.
- **Path-based scoping at the URL level.** A session can target `/mcp` (full org), `/mcp/:org` (single org), or `/mcp/:org/:project`. The OAuth `resource` parameter binds the token to that path; tokens cannot be reused against a broader path. **This is novel** — we don't have to handle it for PostHog.
- **`grantedSkills` system** controls which tools light up. Sentry has `--disable-skills=seer` CLI / `MCP_DISABLE_SKILLS` env var. We'd want a meta-tool similar to PostHog's config tools, except the Sentry knob is "disable categories" rather than "enable categories."
- **`regionUrl` parameter** validated against an allowlist server-side to prevent SSRF. We don't generate this — the user picks at consent.
- **Embedded-agent provider configurable** via `EMBEDDED_AGENT_PROVIDER` (`openai` or `anthropic`); without an LLM key the embedded-agent tools are unavailable.
- **Two-tier error model** (`UserInputError` vs system errors logged to Sentry).
- Self-hosted Sentry: `--host` and `--insecure-http` flags. Aggregator must accept a configurable instance host per connection — same as GitLab.

## Effort

**High.** This connector wants its own design exercise:

- `default_scope = "org:read project:write team:write event:write"` at minimum.
- `connectors/sentry.py` with three meta-tools: `sentry-set-org`, `sentry-set-project`, `sentry-set-skills`. Each updates per-session config that rebuilds the upstream URL (PostHog-pattern).
- Path-scoping handling: when the user picks org/project, we rebuild the upstream URL to `/mcp/:org/:project`, NOT pass them as headers. The token needs to be issued against the new path — meaning re-auth on org change.
- Optional but valuable: surface `whoami` early so the agent knows what's available without spamming `find_organizations` first.

## Sources

- https://github.com/getsentry/sentry-mcp (README + AGENTS.md)
- https://raw.githubusercontent.com/getsentry/sentry-mcp/main/docs/security.md
- https://raw.githubusercontent.com/getsentry/sentry-mcp/main/docs/architecture.md
