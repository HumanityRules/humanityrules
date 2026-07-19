# Webapps Design

How a Hermes agent builds, runs, and serves user-built web apps (any language: Python, Node, Elixir, Go, …) at dash-prefixed hostnames (`<slug>-<agent-host>`). Environment-level DNS and TLS cover every hostname; creating a webapp requires no infrastructure work.

## The core constraint

The user types into the agent: "build me a dashboard." The agent writes code, runs it, and tells the user *where to click*. That URL must be on the agent's **own** hostname tree (`https://...<agent-host>/...`) — anything entirely elsewhere means explaining "why does my dashboard live on a different host than my agent?"

HUMR controls Route 53, ACM, and the shared ALB for every customer environment. A single `*.<zone>` DNS record and certificate cover the agent root and its dash-prefixed webapp hosts. Host-keyed routing inside the container handles each webapp without adding cloud resources.

## Hostname model

Each user webapp hostname sits one label deep under the environment zone. Its leftmost label joins the webapp slug and dashless agent label as `<slug>-<agent-label>`. For example, the `dashboard` webapp served by `wolfie.humr.io` is available at `dashboard-wolfie.humr.io`, covered by the environment's `*.humr.io` certificate. The app sees `/` as its public root, so absolute paths such as `/assets/...`, `/api`, and `/ws` work naturally across frameworks and prebuilt SPAs.

Agent hostname labels are dashless (`[a-z0-9]+`). User webapp slugs may contain dashes, so consumers split a webapp hostname at the last dash to recover the app slug and agent label unambiguously.

Platform-internal slugs (`__*`) use `<agent-host>/webapps/<slug>/`. Their WebUI consumers are already on the agent host, so this path keeps those calls same-origin. User slugs always use dash-prefixed hosts.

Caddy performs the L7 routing per request and supports WebSockets, SSE, streaming responses, HTTP/1.1 keep-alive, and HTTP/2 multiplexing. Its single binary runs beside the WebUI in the Hermes container.

## The topology

```
Browser
  │
  ▼
ALB :443 (host: <agent-host>  OR  <app>-<agent-host>)
  │
  ▼  (one listener rule per agent; host condition matches bare + *-<agent-host>)
ECS task — two containers in shared network namespace:

  ┌────────────────────────┐  ┌───────────────────────────────────┐
  │ policy-proxy container │  │ hermes container (nono sandbox)   │
  │                        │  │                                   │
  │ uvicorn :8788 ────────┐│  │ Caddy :8787 ─── <slug>-<host> ──→ │
  │ (auth gate, JWT,      ││──┘                                   │
  │  per-request PDP)     ││──→ bare <host> + path /webapps/__* ─→ │
  │                        │  │ Hermes WebUI :8789 / __admin       │
  │  → upstream :8787      │  │                                   │
  │                        │  │ system process-compose :9956      │
  │                        │  │ webapps process-compose :9957     │
  │                        │  │                                   │
  │                        │  │ user webapps :4000–4019           │
  └────────────────────────┘  └───────────────────────────────────┘
```

Four things to notice:

- **Policy-proxy already terminates auth at port 8788** (the ALB target). It forwards authenticated traffic to `127.0.0.1:8787`, where Caddy listens. Containers in this AppTemplate share a network namespace (awsvpc), so policy-proxy on 0.0.0.0:8788 and Caddy on :8787 talk loopback-to-loopback.
- **One ALB target group and one listener rule per agent.** When webapp hosts are enabled, the ALB host condition contains `<agent-host>` and `*-<agent-host>`. Both values forward to the same target group, policy proxy, and Caddy instance.
- **Caddy keys on the forwarded host.** `<slug>-<agent-host>` maps to the webapp's loopback port from a generated matcher in `routes.caddy`. Bare `<agent-host>` maps to WebUI on **8789**, with path-based routing for `__*` platform-internal slugs such as `__admin`.
- **Auth covers every hostname in the environment.** The session cookie is scoped `Domain=.<env-domain>`, and the control-plane return-URL validator accepts hosts under the environment domain. Dash-prefixed webapp hosts use the same login flow as the agent root.

## The supervisor: process-compose

[process-compose](https://github.com/F1bonacc1/process-compose) is a single Go binary that supervises long-running processes from a YAML file. Hermes runs two independent process-compose daemons inside nono:

1. **System daemon (`127.0.0.1:9956`)** supervises `system.webui` and `system.gateway`. The integrations broker talks to this daemon when it needs to restart one system process after managed env changes.
2. **Webapps daemon (`127.0.0.1:9957`)** supervises `__admin` and user webapps. The `webapps` CLI talks only to this daemon when it applies changes, so user webapp lifecycle commands never bounce WebUI or interrupt active chat streams.

**State lives under `/workspace/`, split between user-facing artifacts and HUMR supervision config:**

```
/workspace/webapps/
  projects/<slug>/                              # user code lives here
  logs/<slug>.log                               # one plain-text stdout/stderr log per app

/workspace/.config/process-compose/
  system/process-compose.yaml                   # system daemon source-of-truth
  system/process-compose.log                    # system daemon's own internal process-compose log
  system/.system.lock                           # flock target for system seed writes
  webapps/process-compose.yaml                  # webapps daemon source-of-truth
  webapps/process-compose.log                   # webapps daemon's own internal process-compose log
  webapps/.webapps.lock                         # flock target for webapp mutations

/workspace/.config/caddy/
  routes.caddy                                  # generated routes file (rewritten on every CLI mutation)
```

`webapps/process-compose.yaml` is the **only** source of truth for webapps. `routes.caddy` is fully derived from it: every CLI mutation rebuilds the file from scratch by walking the YAML's enabled processes. The port lives in one place — the process's `environment: [WEBAPP_PORT=<port>]` — and the route generator reads it from there. No shadow copies, no synchronization concerns.

The split keeps `/workspace/webapps/` as a pure user-data directory (their projects, their logs) and parks HUMR-internal supervision config under `/workspace/.config/` alongside other tools' state (Caddy already writes `.config/caddy/autosave.json` there). These paths are hermeswebui-owned so the sandbox can mutate them; the broker (root) can still read them from outside the sandbox if needed.

`/workspace` is on the persistent root, so this layout survives container restarts. On cold start, each process-compose daemon reads its own YAML and restores supervision; Caddy boots with the existing `routes.caddy` (which the last CLI mutation left correct) and routes are back instantly.

## The agent's contract: the `webapps` CLI

The agent uses a single Python CLI on PATH for normal operations. It never edits `routes.caddy` directly, and it should not edit `webapps/process-compose.yaml` by hand for routine changes. `webapps reload` exists to resync the daemon/routes from the YAML source of truth after drift or an explicit break-glass repair that the CLI cannot express.

```
webapps create <slug> --command "..." --cwd <path> [--env KEY=VALUE ...]
webapps list
webapps logs <slug> [-f]
webapps start <slug> [--timeout 75]
webapps stop <slug>
webapps restart <slug>
webapps reload
webapps set-env <slug> KEY=VALUE [KEY2=VALUE2 ...]
webapps unregister <slug>
webapps delete <slug> --yes
```

`/opt/humr/runtime/webapps`, ~310 lines, shebang pinned to `/opt/hermes/webui/venv/bin/python3` (it imports pyyaml, which the system python doesn't have but the Hermes serving venv does). All YAML mutations are wrapped in `flock /workspace/.config/process-compose/webapps/.webapps.lock` so concurrent invocations don't tear writes. The CLI's `regenerate_routes(doc)` is called inside the lock on every mutation; it rewrites `routes.caddy` end-to-end from the YAML.

**Key contract decisions:**

- **`create` errors on collision.** If the slug exists, the agent must `unregister` it first or pick another slug. No "create-or-update."
- **Port allocation is automatic.** The CLI scans the YAML, picks the next free port in 4000–4019 (the range is allowlisted in the nono profile), and writes `WEBAPP_PORT` into the process's env. The agent's `--command` references `$WEBAPP_PORT`. The route generator parses `WEBAPP_PORT` back out of the YAML — single encoding.
- **`create` is registration-only for user apps.** It writes a disabled YAML entry, accepts repeated `--env KEY=VALUE` flags, regenerates routes, and returns without talking to the process-compose daemon or waiting for readiness. That lets the agent set env/config before the first process start.
- **Readiness gating lives on `start`.** `webapps start <slug>` removes `disabled: true`, regenerates `routes.caddy`, runs `process-compose project update`, then polls until process-compose reports `is_ready == "Ready"` (or `--timeout` expires). `--timeout` is only the CLI's outer polling budget; process-compose's readiness probe controls whether the child is stopped/restarted while the CLI waits. Readiness gates the CLI's success claim, not registration: if start times out, the agent should inspect logs, fix/restart, or unregister/recreate the app.
- **Readiness probe is a TCP open/close check.** process-compose has only `exec` and `http_get` probes (no native `tcp_socket`), so the CLI emits an `exec` probe that opens `/dev/tcp/127.0.0.1/<port>` without writing request bytes. The generated probe starts after 5 seconds, checks every 10 seconds, and allows 6 consecutive failures before process-compose stops/restarts the app. That gives slow-start apps about a minute to bind the port while still surfacing genuinely broken processes. The probe tells you the app accepted a TCP connection; it doesn't tell you the app is *correct*. That's the bare minimum we want for `webapps start` to claim success.
- **`stop` sets `disabled: true` and regenerates `routes.caddy`** (the disabled entry is skipped, so the route disappears). `start` reverses it.
- **`unregister` is non-destructive.** Removes the YAML entry, regenerates routes, and runs `project update`, but leaves `projects/<slug>/` and the existing log file in place. This is the normal path for recreating a bad registration without losing source.
- **`reload` resyncs from the YAML source of truth.** It reloads `/workspace/.config/process-compose/webapps/process-compose.yaml` into the webapps daemon and regenerates `routes.caddy`. It is for YAML/daemon/routes drift and explicit break-glass repairs; normal changes should use typed CLI mutations.
- **`delete` is total.** Removes the YAML entry, regenerates routes (so the route is gone), removes the log file, and `rm -rf projects/<slug>/`. The skill tells the agent to confirm explicitly with the user before passing `--yes`.
- **`set-env` ships in v1.** It updates and restarts only the affected process while preserving the app's source and logs.

## The Caddy route blocks (per-app, in `routes.caddy`)

Two shapes, depending on slug. The CLI's `regenerate_routes(doc)` picks the right one per process and rewrites the file end-to-end on every mutation.

**Routing matches on `X-Forwarded-Host`, not `Host`.** Policy-proxy strips the browser's `Host` (httpx replaces it with the upstream `127.0.0.1:8787` when forwarding) and copies the original value into `X-Forwarded-Host`. Caddy's `host` matcher reads `r.Host`, which by the time the request reaches Caddy is loopback — so every route block uses `header X-Forwarded-Host …` to read the value policy-proxy preserved for us.

**User slug → X-Forwarded-Host-matched site block (the common case):**

```caddy
@webapp_<slug> header X-Forwarded-Host <slug>-<agent-host>
handle @webapp_<slug> {
    reverse_proxy 127.0.0.1:<port> {
        header_up X-Forwarded-Host {header.X-Forwarded-Host}
    }
}
```

The app sees `/` as its public root because the actual path is `/`. Absolute paths (`/assets/...`, `/api/...`, `/ws`) work without any per-framework configuration. The `header_up X-Forwarded-Host` propagates policy-proxy's preserved value on to the upstream; without it, Caddy's reverse_proxy default of "set X-Forwarded-Host from the inbound Host" would substitute the `127.0.0.1:8787` loopback host policy-proxy gave Caddy, and link helpers / OpenAPI server URLs / OAuth callbacks would see the wrong hostname.

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

    @bare header X-Forwarded-Host {$HUMR_PUBLIC_HOSTNAME}
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

`{$HUMR_PUBLIC_HOSTNAME}` is Caddy's parse-time env-var interpolation. The variable is exported by the env-bearer overlay and survives the nono sandbox env scrub (see "Public hostname injection" below).

The two trailing `handle` blocks are mutually exclusive with the per-slug `handle` blocks imported above them: Caddy picks the *first matching* `handle` per request. `@bare` fires for the agent's own host (everything not already claimed by a per-slug block — WebUI, plus path-based `__admin`). The empty trailing `handle` is the catch-all for unmatched hosts and returns an explicit 404.

## Network: nono profile additions

The hermes container runs inside a nono sandbox. Four additions to `hermes-nono-profile.json`:

- **`network.listen_port`**: adds `8788` (technically owned by policy-proxy, but the shared netns means we'd see EADDRINUSE without it being allowlisted), `8789` (WebUI's new home), `9956` (system process-compose admin), `9957` (webapps process-compose admin), and `4000–4019` (user webapps). nono profile JSON uses `Vec<u16>`, no range syntax — the 20 ports are listed individually.
- **`network.open_port`**: same set, plus the existing 9901–9904 / 9950–9952 for AWS signer + integrations broker.
- **`filesystem.read_file`**: adds `/opt/humr/bin/{caddy,process-compose,webapps}` and `/opt/humr/runtime/{Caddyfile,webapps}` so the sandbox can exec them.
- **`environment.allow_vars`**: adds `HUMR_PUBLIC_HOSTNAME` so the CLI can print real URLs (see below).

## Public hostname injection

The agent prints the URL for the user to click. To do that, it needs to know the public hostname. The container doesn't know it by default — it just sees its own loopback addresses.

**Solution: the CDK extends the env-bearer overlay with `HUMR_PUBLIC_HOSTNAME`** (`{subdomain}.{shared_alb_hosted_zone}`) when both are present. Every container with `requires_env_bearer=True` gets it injected at task-definition build time. The nono profile allow-lists the variable so it survives the sandbox env scrub. Both the `webapps` CLI's `url_for(slug)` *and* Caddy's `{$HUMR_PUBLIC_HOSTNAME}` (in the Caddyfile and in the routes generated by the CLI) read it. If missing in the CLI, it prints `<your-agent-hostname>` as a placeholder. The skill instructs the agent to (i) always render the URL as a clickable markdown link, and (ii) substitute the user-visible hostname from the browser's address bar if the placeholder appears.

## Environment DNS, TLS, and agent routing (CDK)

The environment owns its hosted zone exclusively. `EcsClusterStack` in `deploy_base.py` provisions the two resources shared by every agent and webapp hostname in the zone:

- **Wildcard ACM certificate: `*.<zone>`.** It is the HTTPS listener's default certificate and covers every single-label agent root and webapp host in the zone.
- **Wildcard Route 53 A alias: `*.<zone>` → shared ALB.** This is the environment's only DNS record. It is A-only and targets the ALB by alias.

`AppStack._setup_shared_alb_routing` creates the per-agent listener rule. Its host condition is `[<agent-host>]`. When `AppConfig.enable_webapp_hosts` is true, the condition is `[<agent-host>, *-<agent-host>]`. `app_config_builder.py` projects this value from `AppTemplate.enable_webapp_hosts`; Hermes Personal enables it because its runtime includes the user-webapp CLI and Caddy routes.

App stacks create no DNS records, certificates, or listener-certificate attachments. Adding and removing user webapps changes only the persistent process-compose YAML and derived Caddy routes inside the agent runtime.

## Two known restrictions on what runs inside

**Apps must bind to `127.0.0.1`, not `0.0.0.0`.** Caddy is the only thing that should be reachable from outside the container — apps go through Caddy's reverse_proxy, no shortcut. Many frameworks default to all-interfaces; they need explicit configuration. The skill calls this out in the Don'ts.

**Phoenix needs explicit endpoint binding.** Phoenix's HTTP port is configurable; generated apps usually read `PORT`, so `PORT=$WEBAPP_PORT mix phx.server` is the right dev-server shape when the endpoint is configured to bind loopback. For durable apps, **Phoenix releases are the preferred shape**: `MIX_ENV=prod mix release`, then register the release binary with `webapps create --command` using the same `127.0.0.1:$WEBAPP_PORT` binding and launch it with `webapps start`. WebSocket support itself is unaffected — Caddy's `reverse_proxy` upgrades transparently. Phoenix lives at the root of its dash-prefixed host, so `URL_PATH_PREFIX` and LiveSocket-URL rewriting are unnecessary.

## Lifecycle: cold start

ECS replaces the task. persistent-root-runner restores `/workspace/` from the persistent volume. webui.sh starts inside nono and:

1. Registers `__admin` into the webapps YAML; this also creates the webapps layout and Caddy route file if missing.
2. Registers `system.gateway` and `system.webui` into the system YAML; this also creates the system project layout if missing.
3. Starts system process-compose on `9956` and webapps process-compose on `9957`.
4. Waits for WebUI on 8789 to become healthy.
5. Starts Caddy with `--watch`. Caddy reads the existing `routes.caddy` (left in correct state by the last CLI mutation before shutdown) and routes are live immediately.

`webapps list` preserves each app's state across a cold start, modulo a few seconds of "Pending → Running" while processes initialize.

End-to-end verified on `hermesvmendiwebapps`: created `persist-test`, killed the task with `restart-task`, replacement task came up, `webapps list` showed `persist-test` Running+Ready automatically, HTTP 200 served at the original URL.

## Admin webapp & sidebar UI

The WebUI's "Web Apps" panel is a thin reader on top of a **platform-owned webapp**, `__admin`. It is registered in `webapps/process-compose.yaml` and the WebUI extension fetches `/webapps/__admin/api/webapps` same-origin. The path goes through policy-proxy → Caddy → loopback to the FastAPI admin process. **The `__admin` slug is path-routed on the bare agent host** so the WebUI extension's same-origin fetch works without CORS. All `__*` slugs use this internal routing shape.

This buys three things:

- **Zero new surface.** No Caddy admin allow-list, no second sidecar, no per-endpoint auth bypass. If the webapps mechanism breaks, the panel breaks too — and that's actually what we want during a regression: one symptom, one diagnosis.
- **Room to grow.** The slug is `__admin`, not `__webapps`. The same FastAPI process can host future runtime-admin endpoints (logs viewer, runtime ops) without ever putting "HUMR" in a URL or carving a second admin path.
- **Plain HTTP between browser and backend.** The WebUI extension is the only client; the API speaks ordinary JSON. No SSE, no WebSocket, no integrations broker.

**Reserved-prefix convention.** The slug regex (`webapps_lib.SLUG_PATTERN`) accepts an optional `__` prefix. There is **no enforcement** in the CLI — a `__` slug is a Python-dunder-style hint that "this is platform internal," not a hard reservation. The bootstrap (`webapps create __admin --if-missing --bootstrap-enabled` in `webui.sh`) wins the cold-start race and registers the slug; subsequent agent attempts to create the same slug collide on the existing entry and error, which is the same behavior as any other slug collision. The skill's Don'ts tell the agent not to touch `__*` slugs.

**Source layout.** `template_repos/hermes_agent/humr_runtime/webapps/admin/`. `__main__.py` reads `WEBAPP_PORT` from the env (set by the supervisor like for any webapp) and serves `server.py`'s FastAPI `app` on `127.0.0.1:$WEBAPP_PORT`. Boot order in `webui.sh`: `webapps create __admin --if-missing --bootstrap-enabled` writes an enabled webapps YAML entry and route before the daemon starts; system entries are seeded separately; both process-compose daemons start; WebUI health gates Caddy startup. The `--if-missing` flag is idempotent; on a redeploy where `__admin` is already in the YAML, the bootstrap is a no-op.

**v1 surface.** Read-only:

- `GET /api/webapps` — list with slug/port/status/is_ready/restarts/routed/url/is_internal.
- `GET /api/webapps/{slug}` — detail (adds command/working_dir/environment).
- `GET /api/webapps/{slug}/logs?tail=N` — last N log lines (capped at 2000).

No mutation endpoints: start/stop/restart/delete stay on the CLI. The panel polls every 3s while active and stops when the user navigates away.

**WebUI extension.** Hermes' `HERMES_WEBUI_EXTENSION_SCRIPT_URLS` accepts a comma-separated list (validated by `apptoo/api/extensions.py:_read_url_list`), so HUMR ships two parallel files: `humr-integrations.js` / `humr-integrations.css` and `humr-webapps.js` / `humr-webapps.css`. Both wrap the upstream `switchPanel` (chained, each with its own `__humr*Wrapped` flag) and contribute one rail icon + one sidebar pane + one main view. The webapps panel filters out `__*` slugs by default so the user sees only their own apps.

## Out of scope (v1)

- **Non-HTTP background workers.** A Discord bot, cron job, or queue consumer has no port or Caddy route. The `webapps` CLI manages HTTP applications only.
- **Mutations from the UI.** No start/stop/restart/delete buttons in the Web Apps panel. The CLI is the sole mutation surface in v1; the panel is a status reader.
- **Resource limits per app.** A user app eating 100% CPU starves Hermes. process-compose doesn't do cgroup limits; ECS task-level limits exist but per-app limits don't. Not fixed in v1.
- **Trash-bin on delete.** `webapps delete --yes` is total: route, supervision, logs, and `projects/<slug>/`. The skill mandates explicit user confirmation before passing `--yes`.

## Files of interest

- **`template_repos/hermes_agent/humr_runtime/webapps/Caddyfile`** — the static config Caddy loads at boot.
- **`template_repos/hermes_agent/humr_runtime/webapps/webapps`** — the CLI. Thin shim over `webapps_lib.py`.
- **`template_repos/hermes_agent/humr_runtime/webapps/webapps_lib.py`** — shared helpers (slug pattern, YAML I/O, route generation, process-compose RPC). Imported by both the CLI and the admin webapp.
- **`template_repos/hermes_agent/humr_runtime/webapps/admin/`** — the `__admin` FastAPI webapp (`server.py` + `__main__.py`).
- **`template_repos/hermes_agent/webui-extension/humr-webapps.{js,css}`** — the Web Apps sidebar panel.
- **`template_repos/hermes_agent/humr_runtime/webui.sh`** — launches Caddy + process-compose + WebUI inside nono, bootstraps `__admin`, propagates failures.
- **`template_repos/hermes_agent/humr_runtime/supervisor.sh`** — exports `HERMES_WEBUI_PORT=8789` so WebUI clears port 8787 for Caddy.
- **`template_repos/hermes_agent/humr_runtime/hermes-nono-profile.json`** — port allow-lists, binary read-allows, `HUMR_PUBLIC_HOSTNAME` allow_vars entry.
- **`template_repos/hermes_agent/Dockerfile`** — downloads Caddy + process-compose binaries; symlinks the CLI onto PATH.
- **`template_repos/hermes_agent/skills/development/webapps/SKILL.md`** — agent-facing contract, worked example, Don'ts.
- **`humanityrules_app/services/infra_customer/deploy_base.py`** — provisions the environment's `*.<zone>` certificate, wildcard A alias, and shared ALB listeners.
- **`humanityrules_app/services/infra_customer/deploy_app.py`** — injects `HUMR_PUBLIC_HOSTNAME` into the env-bearer overlay and creates each agent's ALB rules with the optional `*-<agent-host>` condition.
- **`humanityrules_app/services/infra_customer/appconfig.py`** — defines `AppConfig.enable_webapp_hosts`, which controls the webapp-host ALB condition.
- **`humanityrules_app/models.py`** — stores `AppTemplate.enable_webapp_hosts`; `app_config_builder.py` projects it into `AppConfig` at build time.
