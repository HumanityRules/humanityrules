# DCR candidates

Per-vendor research on Group 1 vendors from the parent
`../merge_connectors_auth_audit.md` audit — vendors that host an official
remote MCP server with OAuth Dynamic Client Registration (the "Notion
model"), and which we therefore could integrate directly into Hermes
without going through Merge.

Read **[SUMMARY.md](SUMMARY.md)** first. It distills the per-vendor
verdicts into an implementation order, an inventory of mechanisms beyond
the Notion baseline, and the work blocked by vendor allowlists.

## Files

- [SUMMARY.md](SUMMARY.md) — cross-cut, recommended order, mechanisms
  inventory.
- One file per candidate vendor:
  - [airtable.md](airtable.md)
  - [asana.md](asana.md)
  - [atlassian.md](atlassian.md) — covers `jira`, `jira_service_management`, `confluence`
  - [attio.md](attio.md)
  - [canva.md](canva.md)
  - [clickup.md](clickup.md)
  - [contentful.md](contentful.md)
  - [datadog.md](datadog.md)
  - [figma.md](figma.md)
  - [gitlab.md](gitlab.md)
  - [intercom.md](intercom.md)
  - [klaviyo.md](klaviyo.md)
  - [linear.md](linear.md)
  - [lucidchart.md](lucidchart.md)
  - [make.md](make.md)
  - [miro.md](miro.md)
  - [monday.md](monday.md)
  - [paypal.md](paypal.md)
  - [ramp.md](ramp.md)
  - [sanity.md](sanity.md)
  - [sentry.md](sentry.md)
  - [square.md](square.md)
  - [stripe.md](stripe.md)
  - [webflow.md](webflow.md)
  - [wix.md](wix.md)

Two of the original Group-1 vendors — `notion` and `posthog` — are not in
this directory because they are already integrated (Notion landed) or in
flight (PostHog worktree).
