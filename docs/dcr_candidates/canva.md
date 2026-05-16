# Canva

`https://mcp.canva.com/mcp`

## DCR status

- AS metadata `https://mcp.canva.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.canva.com/register`. DCR live-tested.
- `scopes_supported = []` on the AS metadata.
- **But: Canva publishes a granular Connect API scope catalog** that the OAuth server enforces — see "Mechanisms beyond Notion" below. The empty `scopes_supported` is misleading.

Status: live, no explicit GA/beta label. Plan-conditional capabilities (Enterprise gates autofill).

## Did Merge do a good job?

**Acceptable.** Merge ships **35 tools** for Canva. Canva's docs do not enumerate the MCP tool surface verbatim, but the Connect API areas (assets, designs, brand templates, comments, folders, users) suggest the underlying surface is roughly similar in shape.

Documented Canva MCP capabilities:
- Create empty designs
- Autofill brand templates with content (Enterprise-gated)
- Find existing designs
- Export designs as PDFs/images

Merge's 35 tools are likely deeper than the documented MCP surface, since Merge wraps the full Connect API.

## Would direct integration be advantageous?

**Marginal.** Direct could win on always-fresh capabilities and on respecting Canva's plan gating natively. Merge already covers the meat. No standout reason to prioritize this one.

## Mechanisms beyond Notion

- **Granular per-resource scopes (read ≠ write).** Closest match to Stripe's posture among the easy candidates. Aggregator should request:
  - `asset:read`, `asset:write`
  - `brandtemplate:content:read`, `brandtemplate:meta:read`
  - `design:content:read`, `design:content:write`, `design:meta:read`
  - `comment:read`, `comment:write`
  - `folder:read`, `folder:write`, `folder:permission:write`
  - `openid`, `profile`, `email`, `profile:read`
  - `collaboration:event` (webhooks, probably skip)
- **Strict scope hygiene.** `:write` does NOT imply `:read`. Aggregator must request both explicitly when both are needed.
- **Plan-conditional tool visibility.** Autofill is Enterprise-only; tools may be hidden depending on plan. Don't cache tool lists across users.
- **No URL-level narrowing** (`?features=`, `?tools=`).

## Effort

**Medium.** ~PostHog-shaped scope handling, but no per-session config or meta-tools.

- `default_scope` listing every read+write pair we want at consent.
- Plan gating handled at runtime (the upstream just hides tools we can't call).

## Sources

- https://www.canva.com/help/mcp-agent-setup/
- https://www.canva.dev/docs/connect/appendix/scopes/
- https://www.canva.dev/docs/connect/api-reference/
- https://www.canva.dev/docs/connect/llms.txt
