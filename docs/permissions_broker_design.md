# Permissions Editor in Hermes — Design

How the IAM task-role permissions editor moves from the HUMR web UI into the Hermes
WebUI, so a Hermes agent deployment can request AWS permissions **for its own task
role** without any HUMR credential entering the sandbox.

## Scope

- **This editor is self-referential.** It edits permissions for *this* Hermes
  deployment's own task role — the `(app, environment)` is fixed and implicit,
  resolved by the broker from its own identity (`HUMR_APP_SLUG` + the env-scoped
  bearer). There is exactly one `AppPermissionRequest` of interest.
- **Full breadth.** All curated services plus the full IAM service catalog, the
  five access levels (`Read`, `Write`, `List`, `Tagging`, `Permissions
  management`), and per-service resource pickers — the same breadth as the HUMR
  editor.
- **The HUMR editor stays.** `humanityrules_app/views/security_permissions_editor.py`
  remains the operator tool for editing *any* app's permissions. This is a new,
  narrower surface, not a migration. Both UIs sit on the same service layer
  (`humanityrules_app/services/permissions.py`) and models (`AppPermissions`,
  `AppPermissionRequest`).
- **Phase 1 = editor only, no agent.** We port the editor pane verbatim
  (behavior, not the two-pane layout) and ship it standalone. The agent + skill
  come in phase 2. See "Phasing" below.

## The request flow

Two callers, one relay, one brain. The only thing that differs between phases is
the *first hop*:

```
PANEL (browser)  ──/__humr_broker/permissions/*──► Caddy ─┐
                                                          ├─► control_api (127.0.0.1:9951) ──► HUMR /api/permissions/*
AGENT (sandbox)  ──127.0.0.1:9951/permissions/*──────────┘                                    (phase 2)
```

- **The panel** (a Hermes WebUI extension, browser JS —
  `template_repos/hermes_agent/webui-extension/humr-permissions.js`) reaches the
  broker same-origin via the existing `/__humr_broker/*` reverse-proxy route — the
  same one the integrations panel uses (`patches-webui/07-humr-broker-proxy.patch`).
  No new proxy patch is needed; permissions is just a new path group. **This file
  is the sole browser consumer of the contract below — any change to the
  service_group / statement-mutation shape must update it (it has no automated
  test).**
- **The agent** (phase 2, in the nono sandbox) reaches the same control_api
  directly at `127.0.0.1:9951` — loopback, reachable because `NO_PROXY` includes
  `127.0.0.1` and the broker control port is exempt from the HTTPS proxy
  (`humr_runtime/supervisor.sh`).
- **control_api stays pure transport.** Its handlers parse and forward; they hold
  no per-request secret and make no authorization decision. The broker's
  `HumrClient` attaches the app bearer on the outbound call.
- **HUMR is the brain.** It validates the app bearer, derives `(app, environment,
  owner)` from the token — nothing in the request names them — scopes everything
  to the org, and enforces the ABAC `environment:approve` check on Apply. Neither
  the browser nor the agent ever holds the bearer.

## The rename: `integrations_broker` → `humr_broker`

The env-resident broker is no longer integrations-only — it now also fronts the
permissions API, and more HUMR APIs later. The runtime component
(`humr_runtime/integrations/integrations_broker.py` and the integrations-centric
naming around `control_api.py`) is renamed to `humr_broker` to reflect that it is
the single env→HUMR relay, not an integrations-specific one. The URL prefix is
already `/__humr_broker/*`, so this is a code-naming alignment, not a routing
change.

## JSON contract — `/permissions/*`

One logical surface; the panel calls it under `/__humr_broker/permissions`, the
agent (phase 2) under `127.0.0.1:9951/permissions`, both relayed to HUMR
`/api/permissions/*`. The target `(app, environment)` is **never** a parameter —
HUMR resolves it from the deployment identity.

### Object shapes

A **statement** (from `permissions_service.py`). `sid` is a stable per-statement id;
a service may appear in more than one statement, each holding a distinct
access-level/resource scope (e.g. `List` on `*` and `Read` on specific tables). `sid`
is internal to HUMR and is stripped before the policy reaches AWS:

```json
{ "sid": "<hex id>", "service": "<service>", "effect": "Allow",
  "access_levels": ["Read", "Write"],
  "resources": ["arn:aws:<service>:..."] }
```

A **service_group** (render-ready, from `build_service_group_data` — one per statement,
so the list may contain repeated `service` values distinguished by `sid`):

```json
{ "sid": "<hex id>", "service": "<service>", "display_name": "<Service display name>",
  "access_levels": [{"name": "Read", "checked": true}, {"name": "Write", "checked": false}, "..."],
  "resources": ["arn:aws:<service>:..."],
  "available_resources": [{"arn": "arn:aws:<service>:...", "label": "<resource label>", "selected": true}, "..."],
  "resource_placeholder": "Select resource...",
  "has_checked_levels": true, "selected_count": 1 }
```

### Endpoints

1. **Open/resolve draft + full state** — `GET /permissions/draft`
   Resolve-or-create the draft for this deployment (mirrors the
   `security_permissions_editor` GET). Returns:

   ```json
   { "request_id": "uuid", "status": "draft", "description": "...",
     "has_changes": false, "updated_at": "2026-06-16T...",
     "app": {"slug": "...", "name": "..."},
     "environment": {"slug": "...", "aws_account": "..."},
     "service_groups": [ "...as above..." ] }
   ```

2. **Mutate a statement** — `POST /permissions/draft/<request_id>/statement`
   Action-based, verbatim from `security_permissions_editor_update_statement`.
   HUMR's per-service resource handling (e.g. composing a resource ARN from a base
   ARN plus a path/prefix) is preserved server-side:

   ```json
   { "action": "add_level|remove_level|add_service|remove_service|add_resource|remove_resource",
     "service": "<service>", "statement_id": "<sid>", "level": "Read", "arn": "arn:aws:<service>:..." }
   ```

   `add_service` always creates a new statement (so a service can be added more than
   once) and ignores `statement_id`; every other action targets the statement named by
   `statement_id` (the `sid` from a service_group). Returns
   `{ "service_groups": [...], "has_changes": true, "updated_at": "..." }`.
   `409` if status ≠ `draft`.

3. **Description** — `PUT /permissions/draft/<request_id>/description`
   Body `{"description": "..."}` → `204`. Client-debounced, as today.

4. **Cancel** — `POST /permissions/draft/<request_id>/cancel`
   Reset to the `AppPermissions` baseline →
   `{ "service_groups": [...], "has_changes": false }`.

5. **Apply** — `POST /permissions/draft/<request_id>/apply`
   ABAC `environment:approve` enforced on HUMR →
   `{ "status": "approved_pending_apply", "request_id": "..." }`.
   Apply is async (job worker → `applying` → `applied`/`failed`).

6. **Refresh resources** — `POST /permissions/draft/<request_id>/refresh-resources`
   Clear + refetch the AWS resource cache → `{ "service_groups": [...] }`.

7. **Resources for one service** — `GET /permissions/resources?service=<svc>`
   `{ "service": "<service>", "available_resources": [...] }`. Cache-backed; for
   lazy-loading a picker when a service is added.

8. **Service catalog** — `GET /permissions/service-catalog`
   `{ "services": [{"value","label","is_curated"}, ...],
      "access_levels": ["Read","Write","List","Tagging","Permissions management"] }`.

### Decisions baked into the contract

- **Service catalog is its own endpoint (#8), not inlined.** It is the full IAM
  service list — large and static. The panel fetches it once and caches, rather
  than re-shipping it on every mutation (today it is inlined as
  `service_options_json` in the Django template).
- **Mutations return full `service_groups`, not partials.** Today's Django
  returns surgical HTMX partials; a client-rendered panel re-renders from full
  state, which is simpler and avoids partial-merge bugs. Affected-group-only is
  available later if payloads bite.
- **Apply lifecycle = short poll on #1.** With no SSE in phase 1, the panel polls
  `GET /permissions/draft` for the `applying → applied/failed` transition after
  Apply. This is the only poll phase 1 needs, and it is for a job result, not
  agent sync.
- **`updated_at` returned now, etag enforced later.** Single writer in phase 1 →
  no concurrency control yet. Returning `updated_at` lets phase 2 add an
  `If-Unmodified-Since`/etag precondition for the two-writer case with no
  contract change.

## The panel (phase 1)

A new WebUI extension (`humr-permissions.js` / `.css`), wired through
`HERMES_WEBUI_EXTENSION_*` in `humr_runtime/webui.sh` alongside the integrations
and webapps extensions. It follows `vendor/hermes-webui/docs/EXTENSIONS.md`:
extension-owned container IDs, additive, reversible.

- **Mounted full-width as its own rail destination**, the proven
  `humr-integrations.js` pattern (rail button + `#mainPermissions` `.main-view` +
  a `showing-permissions` class wrapper around `switchPanel`). Standalone, so it
  gets full width and sidesteps the sidebar-width question entirely for phase 1.
- **Written as a container-agnostic render module** (renders into a passed-in
  element). Phase 2's relocation — editor → `#panelPermissions` sidebar, chat →
  main view — then becomes a one-line change to the mount target, not a rewrite.
- **Panel is the sole writer in phase 1.** It made every change and gets fresh
  JSON back, so there is no cross-process refresh problem yet (that arrives with
  the agent).

## What lives where

- **HUMR** — new `/api/permissions/*` endpoints (bearer-auth, JSON), reusing
  `services/permissions.py` and the existing models. The session-auth Django HTML
  editor is untouched. HUMR owns auth, target resolution, ABAC, and the async
  Apply job (`permissions_apply_executor`).
- **Broker (`humr_broker`)** — new `/permissions/*` routes on control_api, pure
  transport, relaying to HUMR with the app bearer via `HumrClient`.
- **WebUI extension** — `humr-permissions.js/.css`, the client-rendered editor.

## Phasing

**Phase 1 (this doc's primary scope):**
1. Rename `integrations_broker` → `humr_broker`.
2. HUMR `/api/permissions/*` endpoints (JSON, bearer-auth), reusing the service layer.
3. `humr_broker` control_api `/permissions/*` routes (pure transport).
4. `humr-permissions.js/.css` extension, full-width destination, container-agnostic.

**Phase 2 (deferred):**
- **Agent tools + skill.** Native Hermes plugin tools — `get_permission_draft`,
  `update_permission_draft`, `query_app_logs`, `lookup_access_denied_events` —
  that call the broker; a skill carries the least-privilege reasoning/workflow for
  building a draft. Apply/cancel stay human-only. All tools route agent → broker →
  HUMR; the sandbox never holds the bearer or AWS creds.
- **Layout move.** Editor → `#panelPermissions` sidebar, the dedicated permissions
  conversation → main view. Because a panel named `permissions` is not in
  upstream's main-view-panel set, `switchPanel` keeps `#mainChat` in the main view
  by default — so "editor in sidebar, chat in main" falls out without forking
  vendored WebUI (`vendor/hermes-webui/static/panels.js`). `loadSession` pins the
  dedicated conversation.
- **Live refresh.** With the agent as a second writer, the panel must reflect
  agent edits — poll the draft's `updated_at` first; an SSE bridge from the broker
  later. See `docs/ui_live_update_contract.md` for existing live-update patterns.
- **etag concurrency.** Add an `If-Unmodified-Since`/etag precondition once two
  writers exist, using the `updated_at` already in responses.

## Resolved questions

- **Target binding.** Dissolved by the self-referential scope: exactly one draft
  per deployment, resolved from broker identity. No app/env params, and the
  phase-2 "single conversation vs per-draft" question is moot — there is one
  conversation because there is one draft.
- **Tools vs skill-with-curl.** Native plugin tools for reliability on the write
  path; a skill for reasoning. Not either/or. (Phase 2.)
- **Server-side draft vs context-resident.** Server-side. Context is private to
  the agent and lossy under compaction; the panel and the async Apply job both
  need the draft, and the reviewed artifact must equal the applied artifact.
