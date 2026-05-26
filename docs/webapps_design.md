# Webapps Design

How a Hermes agent builds, runs, and serves user-built web apps (any language: Python, Node, Elixir, Go, …) as per-app sub-subdomains of the agent's hostname (`<slug>.<agent-host>`). One-time wildcard infra at agent-deploy time; zero per-app DNS, cert, or ALB work after that.

## The core constraint

The user types into the agent: "build me a dashboard." The agent writes code, runs it, and tells the user *where to click*. That URL must be on the agent's **own** hostname tree (`https://...<agent-host>/...`) — anything entirely elsewhere means explaining "why does my dashboard live on a different host than my agent?"

The realization that decides this design: DOH already controls Route 53, ACM, and the ALB for every customer environment, so the cost of giving each agent its own wildcard subdomain space is **once per agent at deploy time**, not per webapp. Once that's in place, host-keyed routing inside the container handles new webapps for free.

## The choice space

Two shapes ended up being seriously considered:

1. **Path-prefix under the agent host (`<agent-host>/webapps/<slug>/`).** The original design. Each app is a path-prefixed reverse-proxy route. Costs nothing at deploy time. *Pays a recurring tax* per webapp: frameworks must honor `X-Forwarded-Prefix`, prebuilt SPAs with absolute `/assets/...` paths break, JS-hardcoded `/api` and `/ws` calls need rewriting or shimming, every new framework adds another gotcha to document.
2. **Per-app sub-subdomain (`<slug>.<agent-host>`).** Each webapp gets its own host. Apps see `/` as their public root — no prefix, no `X-Forwarded-Prefix`, no shims, no rewriting. Prebuilt SPAs, dev servers, Phoenix LiveView, ttyd, Streamlit — all just work because they think they're at the root of a host. *One-time tax* at agent deploy: a per-agent wildcard ACM cert, a wildcard Route 53 record, and the agent's ALB listener rule widened to include `*.<agent-host>`.

**Choice: per-app subdomain.** The tax shape inverts: option (1) charges every webapp install, every framework adoption, every prebuilt-SPA case forever; option (2) charges once per agent and never again. The agent-side complexity collapses — Caddy distinguishes apps by Host header instead of stripping a path prefix, the skill loses its per-framework gotcha pages, and the system has no opinion about what an app does with its own URLs.

The earlier design rejected this on the grounds of "propagating DNS, adding ALB listener rules per app." That framing was wrong once DOH owns the DNS+ACM+ALB triad: a single wildcard record/cert/rule covers all of an agent's webapps. Per-webapp cost is zero.

Below the routing-keyed-by decision, the Caddy-sidecar shape from the path-prefix design carries over verbatim. Native WS/SSE/streaming, single ~40MB binary, zero WebUI patches; data-path costs are negligible because Caddy is already optimal at `splice(2)`/HTTP/2 demux/WS upgrades. Earlier alternatives (fd-handoff via `SCM_RIGHTS`, eBPF sockmap, ASGI sub-app, WebUI middleware patch) all founder on HTTP/1.1 keep-alive + HTTP/2 multiplexing making L7 routing a per-*request* decision rather than per-*connection*.

## The topology

```
Browser
  │
  ▼
ALB :443 (host: hermes-<slug>.<env>.com  OR  <app>.hermes-<slug>.<env>.com)
  │
  ▼  (one listener rule per agent; host condition matches bare + *.<agent-host>)
ECS task — two containers in shared network namespace:

  ┌────────────────────────┐  ┌───────────────────────────────────┐
  │ policy-proxy container │  │ hermes container (nono sandbox)   │
  │                        │  │                                   │
  │ uvicorn :8788 ────────┐│  │ Caddy :8787 ──── <slug>.<host> ──→ │
  │ (auth gate, JWT,      ││──┘                                   │
  │  per-request PDP)     ││──→ bare <host> + path /webapps/__* ─→ │
  │                        │  │ Hermes WebUI :8789 / __admin       │
  │  → upstream :8787      │  │                                   │
  │                        │  │ process-compose :9956             │
  │                        │  │                                   │
  │                        │  │ user webapps :4000–4019           │
  └────────────────────────┘  └───────────────────────────────────┘
```

Four things to notice:

- **Policy-proxy already terminates auth at port 8788** (the ALB target). It forwards authenticated traffic to `127.0.0.1:8787`, where Caddy listens. Containers in this AppTemplate share a network namespace (awsvpc), so policy-proxy on 0.0.0.0:8788 and Caddy on :8787 talk loopback-to-loopback.
- **One ALB target group, one listener rule, both hostname shapes.** ALB's host condition matches `<agent-host>` *and* `*.<agent-host>` in the same rule — same target group either way. Per-webapp ALB cost is zero.
- **Caddy keys on Host header.** `<slug>.<agent-host>` → per-app loopback port (from a generated site-block matcher in `routes.caddy`). Bare `<agent-host>` → WebUI on **8789**, plus path-based routing for `__*` platform-internal slugs (e.g. `__admin`) so the WebUI's same-origin extension can call them without CORS.
- **Auth is a non-event for subdomains.** The session cookie is already scoped `Domain=.<env-domain>` (env-wide), and the control-plane auth flow's return-URL validator accepts any host under the env parent domain — so login flows for `<slug>.<agent-host>` use the same machinery as the bare agent host with no auth-side changes.

## The supervisor: process-compose

[process-compose](https://github.com/F1bonacc1/process-compose) is a single Go binary that supervises long-running processes from a YAML file. We picked it after rejecting "write our own watcher" — process-compose's `project update` does surgical reload (only changed processes restart, new ones start, removed ones stop, readiness probes honored), which is exactly what we need.

**State lives under `/workspace/`, split between user-facing artifacts and DOH supervision config:**

```
/workspace/webapps/
  projects/<slug>/                              # user code lives here
  logs/<slug>.log                               # captured stdout/stderr, written by process-compose

/workspace/.config/process-compose/
  process-compose.yaml                          # the supervisor's source-of-truth
  .webapps.lock                                 # flock target for YAML mutations

/workspace/.config/caddy/
  routes.caddy                                  # generated routes file (rewritten on every CLI mutation)
```

`process-compose.yaml` is the **only** source of truth. `routes.caddy` is fully derived from it: every CLI mutation rebuilds the file from scratch by walking the YAML's enabled processes. The port lives in one place — the process's `environment: [WEBAPP_PORT=<port>]` — and the route generator reads it from there. No shadow copies, no synchronization concerns.

The split keeps `/workspace/webapps/` as a pure user-data directory (their projects, their logs) and parks DOH-internal supervision config under `/workspace/.config/` alongside other tools' state (Caddy already writes `.config/caddy/autosave.json` there). All four paths are hermeswebui-owned so the sandbox can mutate them; the broker (root) can still read them from outside the sandbox if needed.

`/workspace` is on the persistent root, so this whole layout survives container restarts. process-compose, on cold start, reads the existing YAML and restores supervision; Caddy boots with the existing `routes.caddy` (which the last CLI mutation left correct) and routes are back instantly.

## The agent's contract: the `webapps` CLI

The agent never touches `process-compose.yaml` or `routes.caddy` directly. It uses a single Python CLI on PATH:

```
webapps create <slug> --command "..." --cwd <path> [--timeout 90]
webapps list
webapps logs <slug> [-f]
webapps start <slug>
webapps stop <slug>
webapps restart <slug>
webapps set-env <slug> KEY=VALUE [KEY2=VALUE2 ...]
webapps delete <slug> --yes
```

`/opt/doh/runtime/webapps`, ~310 lines, shebang pinned to `/opt/hermes/webui/venv/bin/python3` (it imports pyyaml, which the system python doesn't have but the Hermes serving venv does). All YAML mutations are wrapped in `flock /workspace/.config/process-compose/.webapps.lock` so concurrent invocations don't tear writes. The CLI's `regenerate_routes(doc)` is called inside the lock on every mutation; it rewrites `routes.caddy` end-to-end from the YAML.

**Key contract decisions:**

- **`create` errors on collision.** If the slug exists, the agent must `delete` first. No "create-or-update."
- **Port allocation is automatic.** The CLI scans the YAML, picks the next free port in 4000–4019 (the range is allowlisted in the nono profile), and writes `WEBAPP_PORT` into the process's env. The agent's `--command` references `$WEBAPP_PORT`. The route generator parses `WEBAPP_PORT` back out of the YAML — single encoding.
- **Readiness gating.** After `process-compose project update`, the CLI polls until process-compose reports `is_ready == "Ready"` (or timeout). The Caddy route is added to `routes.caddy` **only after** readiness passes — this avoids the brief window where the user's URL would 502 because the upstream isn't accepting connections yet.
- **Readiness probe is a TCP-bind check.** process-compose has only `exec` and `http_get` probes (no native `tcp_socket`), so the CLI emits `bash -c 'echo > /dev/tcp/127.0.0.1/<port>'`. Tells you the app bound the port; doesn't tell you the app is *correct*. That's the bare minimum we want for `webapps create` to claim success.
- **`stop` sets `disabled: true` and regenerates `routes.caddy`** (the disabled entry is skipped, so the route disappears). `start` reverses it.
- **`delete` is total.** Removes the YAML entry, regenerates routes (so the route is gone), removes the log file, and `rm -rf projects/<slug>/`. The skill tells the agent to confirm explicitly with the user before passing `--yes`.
- **`set-env` ships in v1.** Surgical: only the affected process restarts. Without this, every env change would be a delete (now total!) + recreate.

## The Caddy route blocks (per-app, in `routes.caddy`)

Two shapes, depending on slug. The CLI's `regenerate_routes(doc)` picks the right one per process and rewrites the file end-to-end on every mutation.

**Routing matches on `X-Forwarded-Host`, not `Host`.** Policy-proxy strips the browser's `Host` (httpx replaces it with the upstream `127.0.0.1:8787` when forwarding) and copies the original value into `X-Forwarded-Host`. Caddy's `host` matcher reads `r.Host`, which by the time the request reaches Caddy is loopback — so every route block uses `header X-Forwarded-Host …` to read the value policy-proxy preserved for us.

**User slug → X-Forwarded-Host-matched site block (the common case):**

```caddy
@webapp_<slug> header X-Forwarded-Host <slug>.<agent-host>
handle @webapp_<slug> {
    reverse_proxy 127.0.0.1:<port>
}
```

That's the whole route. No `X-Forwarded-Prefix`, no `X-Forwarded-Host` rewrite at the upstream — the app sees `/` as its public root because the actual path is `/`. Absolute paths (`/assets/...`, `/api/...`, `/ws`) work without any per-framework configuration.

**Platform-internal slug (`__*`) → path-matched block on the bare host:**

```caddy
@webapp___admin_root {
    header X-Forwarded-Host <agent-host>
    path /webapps/__admin
}
redir @webapp___admin_root /webapps/__admin/ 308

@webapp___admin {
    header X-Forwarded-Host <agent-host>
    path /webapps/__admin/*
}
handle @webapp___admin {
    handle_path /webapps/__admin/* {
        reverse_proxy 127.0.0.1:<port> {
            header_up X-Forwarded-Host {header.X-Forwarded-Host}
            header_up X-Forwarded-Prefix /webapps/__admin
        }
    }
}
```

Internal slugs stay path-based on the bare host because their *only* consumer is the WebUI extension running at the same host — same-origin `fetch('/webapps/__admin/api/...')` works without CORS gymnastics. The `__` prefix in the slug regex is the cue that flips the routing shape. The `header_up X-Forwarded-Host` line propagates policy-proxy's value to the upstream against Caddy's default behavior of appending the immediate (loopback) Host.

## The full Caddyfile

```caddy
{
    admin off
    auto_https off
}

:8787 {
    import /workspace/.config/caddy/routes.caddy

    @bare header X-Forwarded-Host {$DOH_PUBLIC_HOSTNAME}
    handle @bare {
        reverse_proxy 127.0.0.1:8789 {
            header_up X-Forwarded-Host {header.X-Forwarded-Host}
        }
    }

    handle {
        respond "unknown host" 404
    }
}
```

`admin off` because we never use the admin API; the CLI drives Caddy by rewriting `routes.caddy` and letting `--watch` (passed at startup) pick up the change. `auto_https off` because TLS terminates upstream at the ALB; everything inside the container is plaintext loopback.

`routes.caddy` is seeded with the placeholder content `# no routes` on first boot so it always exists — Caddy's `import` would fail loudly on a missing file.

`{$DOH_PUBLIC_HOSTNAME}` is Caddy's parse-time env-var interpolation. The variable is exported by the env-bearer overlay and survives the nono sandbox env scrub (see "Public hostname injection" below).

The two trailing `handle` blocks are mutually exclusive with the per-slug `handle` blocks imported above them: Caddy picks the *first matching* `handle` per request. `@bare` fires for the agent's own host (everything not already claimed by a per-slug block — WebUI, plus path-based `__admin`). The empty trailing `handle` is the catch-all for unmatched hosts; returns 404 instead of an empty 200.

## Network: nono profile additions

The hermes container runs inside a nono sandbox. Three additions to `hermes-nono-profile.json`:

- **`network.listen_port`**: adds `8788` (technically owned by policy-proxy, but the shared netns means we'd see EADDRINUSE without it being allowlisted), `8789` (WebUI's new home), `9956` (process-compose admin), and `4000–4019` (user webapps). nono profile JSON uses `Vec<u16>`, no range syntax — the 20 ports are listed individually.
- **`network.open_port`**: same set, plus the existing 9901–9903 / 9950–9952 for AWS signer + integrations broker.
- **`filesystem.read_file`**: adds `/opt/doh/bin/{caddy,process-compose,webapps}` and `/opt/doh/runtime/{Caddyfile,webapps}` so the sandbox can exec them.
- **`environment.allow_vars`**: adds `DOH_PUBLIC_HOSTNAME` so the CLI can print real URLs (see below).

## Public hostname injection

The agent prints the URL for the user to click. To do that, it needs to know the public hostname. The container doesn't know it by default — it just sees its own loopback addresses.

**Solution: the CDK extends the env-bearer overlay with `DOH_PUBLIC_HOSTNAME`** (`{subdomain}.{shared_alb_hosted_zone}`) when both are present. Every container with `requires_env_bearer=True` gets it injected at task-definition build time. The nono profile allow-lists the variable so it survives the sandbox env scrub. Both the `webapps` CLI's `url_for(slug)` *and* Caddy's `{$DOH_PUBLIC_HOSTNAME}` (in the Caddyfile and in the routes generated by the CLI) read it. If missing in the CLI, it prints `<your-agent-hostname>` as a placeholder. The skill instructs the agent to (i) always render the URL as a clickable markdown link, and (ii) substitute the user-visible hostname from the browser's address bar if the placeholder appears.

## Per-agent wildcard infra (CDK)

The shape that makes `<slug>.<agent-host>` actually resolve and TLS-handshake at the ALB is provisioned once per agent, in `_setup_shared_alb_routing` of `deploy_app.py`, gated by `AppConfig.enable_subhosting` (which `app_config_builder.py` reads from `AppTemplate.enable_subhosting`). Three resources:

- **Per-agent wildcard ACM cert: `*.<agent-host>`** — DNS-validated through the env's existing hosted zone, so issuance is fully automated. The env-level wildcard cert covers only `<agent-host>` (one label deep); ACM wildcards match a single label, so sub-subdomains need their own cert.
- **Wildcard Route 53 A-alias: `*.<agent-host>` → ALB** — alongside the existing apex record (`<agent-host>` → ALB). One record per agent; new webapps never trigger DNS work.
- **ALB listener-rule host condition widened to `[<agent-host>, *.<agent-host>]`** — same rule, same target group. Per-webapp ALB cost is zero. The wildcard cert is attached to the listener as an SNI cert via `ApplicationListenerCertificate`.

For agents *without* the flag (most templates), the path-prefix mechanism still works — the CLI generates path-based blocks for `__*` slugs on the bare host, the wildcard infra isn't created, and creating a user slug would fail at the ALB because `<slug>.<agent-host>` has no DNS record. In practice only Hermes Personal sets the flag; future templates that ship webapp registration would do the same.

ACM/ALB SNI listener cert count caps at 25 per listener by default (raisable via support ticket). For pre-beta scale that's a non-issue.

## Two known restrictions on what runs inside

**Apps must bind to `127.0.0.1`, not `0.0.0.0`.** Caddy is the only thing that should be reachable from outside the container — apps go through Caddy's reverse_proxy, no shortcut. Many frameworks default to all-interfaces; they need explicit configuration. The skill calls this out in the Don'ts.

**Phoenix needs explicit endpoint binding.** Phoenix's HTTP port is configurable; generated apps usually read `PORT`, so `PORT=$WEBAPP_PORT mix phx.server` is the right dev-server shape when the endpoint is configured to bind loopback. For durable apps, **Phoenix releases are the preferred shape**: `MIX_ENV=prod mix release`, then run the release binary from `webapps create --command` with the same `127.0.0.1:$WEBAPP_PORT` binding. WebSocket support itself is unaffected — Caddy's `reverse_proxy` upgrades transparently. With per-app subdomains, no `URL_PATH_PREFIX` or LiveSocket-URL rewriting is needed — Phoenix lives at the root of its host.

## Lifecycle: cold start

ECS replaces the task. persistent-root-runner restores `/workspace/` from the persistent volume. webui.sh starts inside nono and:

1. Seeds `/workspace/webapps/` if missing (idempotent, only first boot).
2. Starts `process-compose up` against the existing YAML — apps marked enabled come back up automatically; ones marked `disabled: true` stay down (process-compose honors disabled on initial up).
3. Waits for WebUI on 8789 to become healthy.
4. Starts Caddy with `--watch`. Caddy reads the existing `routes.caddy` (left in correct state by the last CLI mutation before shutdown) and routes are live immediately.

`webapps list` after cold start shows everything with the same state it had before, modulo a few seconds of "Pending → Running" while processes initialize.

End-to-end verified on `hermes-vmendi-webapps`: created `persist-test`, killed the task with `restart-task`, replacement task came up, `webapps list` showed `persist-test` Running+Ready automatically, HTTP 200 served at the original URL.

## Admin webapp & sidebar UI

The WebUI's "Web Apps" panel is a thin reader on top of a **platform-owned webapp**, `__admin`. Rather than carve a one-off API path through Caddy → process-compose's admin port, we dogfood the same mechanism the agent uses: `__admin` is registered in `process-compose.yaml` like any other webapp, and the WebUI extension fetches `/webapps/__admin/api/webapps` same-origin. The path goes through policy-proxy → Caddy → loopback to the FastAPI admin process exactly like a user app would. **The `__admin` slug stays path-routed on the bare agent host** (not on a subdomain) so the WebUI extension's same-origin fetch keeps working without CORS — `__*` slugs are the documented exception to the per-app-subdomain rule.

This buys three things:

- **Zero new surface.** No Caddy admin allow-list, no second sidecar, no per-endpoint auth bypass. If the webapps mechanism breaks, the panel breaks too — and that's actually what we want during a regression: one symptom, one diagnosis.
- **Room to grow.** The slug is `__admin`, not `__webapps`. The same FastAPI process can host future runtime-admin endpoints (logs viewer, runtime ops) without ever putting "DOH" in a URL or carving a second admin path.
- **Plain HTTP between browser and backend.** The WebUI extension is the only client; the API speaks ordinary JSON. No SSE, no WebSocket, no integrations broker.

**Reserved-prefix convention.** The slug regex (`webapps_lib.SLUG_PATTERN`) accepts an optional `__` prefix. There is **no enforcement** in the CLI — a `__` slug is a Python-dunder-style hint that "this is platform internal," not a hard reservation. The bootstrap (`webapps create __admin --if-missing` in `webui.sh`) wins the cold-start race and registers the slug; subsequent agent attempts to create the same slug collide on the existing entry and error, which is the same behavior as any other slug collision. The skill's Don'ts tell the agent not to touch `__*` slugs.

**Source layout.** `template_repos/hermes_agent/doh_runtime/admin/` (no "webapps" in the name — scope will grow). `__main__.py` reads `WEBAPP_PORT` from the env (set by the supervisor like for any webapp) and serves `server.py`'s FastAPI `app` on `127.0.0.1:$WEBAPP_PORT`. Boot order in `webui.sh`: start process-compose → wait for `/live` → `webapps create __admin --if-missing` → start WebUI → start Caddy. The `--if-missing` flag is idempotent; on a redeploy where `__admin` is already in the YAML, the bootstrap is a no-op.

**v1 surface.** Read-only:

- `GET /api/webapps` — list with slug/port/status/is_ready/restarts/routed/url/is_internal.
- `GET /api/webapps/{slug}` — detail (adds command/working_dir/environment).
- `GET /api/webapps/{slug}/logs?tail=N` — last N log lines (capped at 2000).

No mutation endpoints: start/stop/restart/delete stay on the CLI. The panel polls every 3s while active and stops when the user navigates away.

**WebUI extension.** Hermes' `HERMES_WEBUI_EXTENSION_SCRIPT_URLS` accepts a comma-separated list (validated by `apptoo/api/extensions.py:_read_url_list`), so DOH ships two parallel files: `doh-integrations.js` / `doh-integrations.css` and `doh-webapps.js` / `doh-webapps.css`. Both wrap the upstream `switchPanel` (chained, each with its own `__doh*Wrapped` flag) and contribute one rail icon + one sidebar pane + one main view. The webapps panel filters out `__*` slugs by default so the user sees only their own apps.

## Out of scope (v1)

- **Non-HTTP background workers.** A Discord bot, a cron job, a queue consumer. Same supervisor would manage them but with no port + no Caddy route. `webapps create --no-port` was considered and rejected — the name `webapps` is a contract, and "background services" is a separate concept worth its own primitive in v2.
- **Mutations from the UI.** No start/stop/restart/delete buttons in the Web Apps panel. The CLI is the sole mutation surface in v1; the panel is a status reader.
- **Resource limits per app.** A user app eating 100% CPU starves Hermes. process-compose doesn't do cgroup limits; ECS task-level limits exist but per-app limits don't. Not fixed in v1.
- **Trash-bin on delete.** `webapps delete --yes` is total: route, supervision, logs, AND `projects/<slug>/`. Considered moving to `.trash/` for recoverability but rejected — too much janitorial complexity for a low-frequency operation. The skill mandates explicit user confirmation before passing `--yes`.

## Files of interest

- **`template_repos/hermes_agent/doh_runtime/Caddyfile`** — the static config Caddy loads at boot.
- **`template_repos/hermes_agent/doh_runtime/webapps`** — the CLI. Thin shim over `webapps_lib.py`.
- **`template_repos/hermes_agent/doh_runtime/webapps_lib.py`** — shared helpers (slug pattern, YAML I/O, route generation, process-compose RPC). Imported by both the CLI and the admin webapp.
- **`template_repos/hermes_agent/doh_runtime/admin/`** — the `__admin` FastAPI webapp (`server.py` + `__main__.py`).
- **`template_repos/hermes_agent/webui-extension/doh-webapps.{js,css}`** — the Web Apps sidebar panel.
- **`template_repos/hermes_agent/doh_runtime/webui.sh`** — launches Caddy + process-compose + WebUI inside nono, bootstraps `__admin`, propagates failures.
- **`template_repos/hermes_agent/doh_runtime/supervisor.sh`** — exports `HERMES_WEBUI_PORT=8789` so WebUI clears port 8787 for Caddy.
- **`template_repos/hermes_agent/doh_runtime/hermes-nono-profile.json`** — port allow-lists, binary read-allows, `DOH_PUBLIC_HOSTNAME` allow_vars entry.
- **`template_repos/hermes_agent/Dockerfile`** — downloads Caddy + process-compose binaries; symlinks the CLI onto PATH.
- **`template_repos/hermes_agent/skills/webapps/SKILL.md`** — agent-facing contract, worked example, Don'ts.
- **`devopshero_app/services/infra_customer/deploy_app.py`** — injects `DOH_PUBLIC_HOSTNAME` into the env-bearer overlay; provisions per-agent wildcard cert + Route 53 record + ALB host condition when `AppConfig.enable_subhosting` is True.
- **`devopshero_app/services/infra_customer/appconfig.py`** — `AppConfig.enable_subhosting` field gating the wildcard-infra provisioning.
- **`devopshero_app/models.py`** — `AppTemplate.enable_subhosting` flag stored on the template; `app_config_builder.py` projects it into AppConfig at build time.
