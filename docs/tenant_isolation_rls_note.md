# Tenant isolation — RLS idea (note to self)

Personal reminder. Pre-beta. Not implemented.

## The problem

Cross-tenant leaks happen when a view fetches a tenant-owned row by a client-supplied id without filtering by the caller's org. Fixed several of these in commit 43d5e3e; the *class* of bug will keep coming back.

## Layered defense (cheap → expensive)

1. **Custom ORM manager** — `OrganizationScopedManager` that raises on `.all()` unless `.scoped(org)` was called first. Catches ~70% in Django code. Reversible.
2. **Semgrep rule in CI** — flag `.filter(id=...)` / `.get(id=...)` without an `organization=` sibling on tenant-owned models. Catches the rest of the ORM cases.
3. **Postgres RLS** — catches raw SQL, signal handlers, anything that goes around the ORM. The floor.

## RLS design (when I do it)

- **Two roles.** `app_admin` (table owner, bypasses RLS). `app_user` (subject to RLS).
- **Middleware** runs `SET LOCAL app.current_org_id = <uuid>` per request from `request.user.current_organization_id`. Requires `ATOMIC_REQUESTS=True` or transaction wrapping.
- **Policy per tenant-owned table.** Direct owner: `organization_id = current_setting('app.current_org_id')::uuid`. Transitive owner (`IntegrationUserCredential` → env → aws_account → org): subquery, with an index on the FK chain.
- **Flag — two options:**
  - **A. Role swap.** `DATABASES["default"]["USER"] = "app_user" if RLS_ENFORCEMENT == "on" else "app_admin"`. Restart-to-flip. Policies stay clean.
  - **B. GUC in policy.** Add `current_setting('app.rls_enforcement', true) IS DISTINCT FROM 'on' OR ...` to every policy. Middleware sets the GUC per request. Live toggle, no restart, but pollutes every policy.
- **Don't use FORCE.** Without it, superuser/migration scripts always work — natural escape hatch.

## Gotchas to remember

- Forgotten `SET LOCAL` → queries return zero rows → empty pages, no errors. The dominant failure mode.
- Celery, management commands, signal handlers, webhook handlers — every entrypoint needs its own GUC setter.
- Multi-org users: the GUC tracks *the org the request is operating on*, not user identity. Switching orgs mid-session must update it.
- Cross-tenant admin views (mine, not customers') run as `app_admin` or with a special "bypass" connection.
- New tenant-owned table → must remember `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `CREATE POLICY`. Worth a CI lint.

## What rolling it out costs

- ~1–2 weeks of integration work (middleware, role split, policies, audit of all entrypoints).
- Trial in staging for a week before prod.
- DDL is reversible; integration code stays even if I abandon.

## Decision deferred

Do the cheap layers first (manager + semgrep). Only reach for RLS if leaks keep finding their way into prod or a customer asks "prove it."
