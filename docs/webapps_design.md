# Webapps Design

How a Hermes agent builds, runs, and serves user-built web apps (any language: Python, Node, Elixir, Go, …) at the same hostname as the WebUI, under `/webapps/<slug>/`. No DNS changes, no per-app ALB rules, no extra hostnames.

## The core constraint

The user types into the agent: "build me a dashboard." The agent writes code, runs it, and tells the user *where to click*. That URL must be on the agent's **own** hostname (`https://hermes-<slug>.<env>/...`) — anything else means propagating DNS, adding ALB listener rules per app, and explaining "why does my dashboard live on a different host than my agent?"

So *something inside the agent's container* has to recognize `/webapps/<slug>/` as non-WebUI traffic and route it to a separate per-app process.

## The choice space

Five shapes were considered before settling on one:

1. **Static-only via a WebUI patch.** A single patch in `patches-webui/` mounts `/webapps/<slug>` to a static directory under `/workspace`. Tiny patch surface, but no Python/Node/Elixir backends — a hard ceiling.
2. **ASGI sub-app inside WebUI.** Mount user Python apps as ASGI children of the WebUI process. Works only for Python; couples user code to the Hermes WebUI lifecycle.
3. **Python reverse-proxy patch in WebUI.** ~200 lines of Starlette middleware in `patches-webui/`. Polyglot. Cost: every Hermes upstream bump is a place the patch can conflict — we already pay this tax on five other patches.
4. **fd-handoff via `SCM_RIGHTS`.** A tiny router peeks the request line, then passes the raw socket fd to the chosen backend over a Unix socket. Beautifully zero-copy. Founders on HTTP/1.1 keep-alive (one connection, many requests on different paths) and HTTP/2 multiplexing — once you've handed the fd to backend A, request #2 going to backend B is wrong. The only escape is forcing `Connection: close`, which kills perf and HTTP/2 entirely.
5. **eBPF sockmap.** Same handoff benefits, in-kernel L7 parsing. Likely blocked on Fargate (no `CAP_BPF` / `CAP_SYS_ADMIN`); HTTP parsing in eBPF's bounded-loop world is genuinely painful.

**Choice: a Caddy reverse-proxy sidecar.** Native WebSocket / SSE / streaming, single ~40MB binary, zero WebUI patches. The data-path cost is fine because Caddy is already optimal at the things that matter (`splice(2)` for zero-copy, native HTTP/2 demux, WS upgrades). The architectural lesson behind rejecting (4)/(5): **HTTP/1.1 keep-alive + HTTP/2 multiplexing make L7 routing a per-*request* decision, not per-*connection*.** Anything wanting to stay out of the data path either pins one backend per connection (breaks the model) or pushes parsing into the kernel (overkill, possibly disallowed). Caddy in the data path is correct, not a compromise.

## The topology

```
Browser
  │
  ▼
ALB :443 (host: hermes-<slug>.<env>.com)
  │
  ▼
ECS task — three containers in shared network namespace:

  ┌────────────────────────┐  ┌───────────────────────────────────┐
  │ policy-proxy container │  │ hermes container (nono sandbox)   │
  │                        │  │                                   │
  │ uvicorn :8788 ────────┐│  │ Caddy :8787  ──── /webapps/X/* ─→ │
  │ (auth gate, JWT,      ││──┘                                  │
  │  per-request PDP)     ││─→ everything else  ───────────────→ │
  │                        │  │ Hermes WebUI :8789                │
  │  → upstream :8787      │  │                                   │
  │                        │  │ process-compose :9956 (admin API) │
  │                        │  │                                   │
  │                        │  │ user webapps :4000–4019           │
  └────────────────────────┘  └───────────────────────────────────┘
```

Three things to notice:

- **Policy-proxy already terminates auth at port 8788** (the ALB target). It forwards authenticated traffic to `127.0.0.1:8787`, where Caddy now listens. Containers in this AppTemplate share a network namespace (awsvpc), so policy-proxy on 0.0.0.0:8788 and Caddy on :8787 talk loopback-to-loopback.
- **Caddy fans out from there.** `/webapps/<slug>/*` → per-app loopback port. Everything else → WebUI on **8789** (was 8787; moved to clear the way for Caddy).
- **Both Caddy and process-compose run inside the existing nono sandbox alongside WebUI.** They have no credentials the LLM mustn't read; their config is derived from agent-written files. The trust boundary rule is: "outside nono only for processes that hold secrets the LLM can't see." Caddy isn't one of those.

## The supervisor: process-compose

[process-compose](https://github.com/F1bonacc1/process-compose) is a single Go binary that supervises long-running processes from a YAML file. We picked it after rejecting "write our own watcher" — process-compose's `project update` does surgical reload (only changed processes restart, new ones start, removed ones stop, readiness probes honored), which is exactly what we need.

**State lives at `/workspace/webapps/`:**

```
/workspace/webapps/
  process-compose.yaml        # the supervisor's source-of-truth
  routes.caddy                # generated routes file (rewritten on every CLI mutation)
  projects/<slug>/            # user code lives here
  logs/<slug>.log             # captured stdout/stderr, written by process-compose
  .lock                       # flock target for YAML mutations
```

`process-compose.yaml` is the **only** source of truth. `routes.caddy` is fully derived from it: every CLI mutation rebuilds the file from scratch by walking the YAML's enabled processes. The port lives in one place — the process's `environment: [WEBAPP_PORT=<port>]` — and the route generator reads it from there. No shadow copies, no synchronization concerns.

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

`/opt/doh/runtime/webapps`, ~310 lines, shebang pinned to `/opt/hermes/webui/venv/bin/python3` (it imports pyyaml, which the system python doesn't have but the Hermes serving venv does). All YAML mutations are wrapped in `flock /workspace/webapps/.lock` so concurrent invocations don't tear writes. The CLI's `regenerate_routes(doc)` is called inside the lock on every mutation; it rewrites `routes.caddy` end-to-end from the YAML.

**Key contract decisions:**

- **`create` errors on collision.** If the slug exists, the agent must `delete` first. No "create-or-update."
- **Port allocation is automatic.** The CLI scans the YAML, picks the next free port in 4000–4019 (the range is allowlisted in the nono profile), and writes `WEBAPP_PORT` into the process's env. The agent's `--command` references `$WEBAPP_PORT`. The route generator parses `WEBAPP_PORT` back out of the YAML — single encoding.
- **Readiness gating.** After `process-compose project update`, the CLI polls until process-compose reports `is_ready == "Ready"` (or timeout). The Caddy route is added to `routes.caddy` **only after** readiness passes — this avoids the brief window where `/webapps/<slug>/` would 502 because the upstream isn't accepting connections yet.
- **Readiness probe is a TCP-bind check.** process-compose has only `exec` and `http_get` probes (no native `tcp_socket`), so the CLI emits `bash -c 'echo > /dev/tcp/127.0.0.1/<port>'`. Tells you the app bound the port; doesn't tell you the app is *correct*. That's the bare minimum we want for `webapps create` to claim success.
- **`stop` sets `disabled: true` and regenerates `routes.caddy`** (the disabled entry is skipped, so the route disappears). `start` reverses it.
- **`delete` is total.** Removes the YAML entry, regenerates routes (so the route is gone), removes the log file, and `rm -rf projects/<slug>/`. The skill tells the agent to confirm explicitly with the user before passing `--yes`.
- **`set-env` ships in v1.** Surgical: only the affected process restarts. Without this, every env change would be a delete (now total!) + recreate.

## The Caddy route block (per-app, in `routes.caddy`)

```caddy
redir /webapps/<slug> /webapps/<slug>/ 308
handle_path /webapps/<slug>/* {
    reverse_proxy 127.0.0.1:<port> {
        header_up X-Forwarded-Host {header.X-Forwarded-Host}
        header_up X-Forwarded-Prefix /webapps/<slug>
    }
}
```

Three lines that all earn their keep:

- **`redir /webapps/<slug> /webapps/<slug>/ 308`** — Caddy's `handle_path /webapps/<slug>/*` matcher requires at least one character after the trailing slash. Without the redir, a bare `/webapps/<slug>` (no slash) falls through to the WebUI catch-all and returns 404. Browsers follow 308 transparently.
- **`header_up X-Forwarded-Host {header.X-Forwarded-Host}`** — policy-proxy already sets `X-Forwarded-Host: hermes-<slug>.<env>.com`. Caddy's `reverse_proxy` would otherwise *append* its own `X-Forwarded-Host: 127.0.0.1:8787` (the immediate Host from policy-proxy's perspective). WebUI's CSRF gate iterates all `X-Forwarded-Host` values, finds the loopback first, and rejects POSTs as cross-origin (403). Replacing the header with the inbound value preserves policy-proxy's intent. (`{header.X-Forwarded-Host}` is the Caddyfile placeholder — `{>X-Forwarded-Host}` is JSON-config syntax and Caddy forwards it as a literal string.)
- **`X-Forwarded-Prefix /webapps/<slug>`** — `handle_path` strips the prefix before forwarding, so the upstream sees a bare `/`. Frameworks that generate absolute URLs (Phoenix LiveView, Express, Rails, FastAPI) use this header to know the public base path. The skill includes per-framework configuration notes.

The same `header_up X-Forwarded-Host` lives in the top-level Caddyfile's WebUI fallback, for the same reason — without it, every WebUI POST through Caddy would 403 with "Cross-origin request rejected."

## The full Caddyfile

```caddy
{
    admin off
    auto_https off
}

:8787 {
    import /workspace/webapps/routes.caddy
    reverse_proxy 127.0.0.1:8789 {
        header_up X-Forwarded-Host {header.X-Forwarded-Host}
    }
}
```

`admin off` because we never use the admin API; the CLI drives Caddy by rewriting `routes.caddy` and letting `--watch` (passed at startup) pick up the change. `auto_https off` because TLS terminates upstream at the ALB; everything inside the container is plaintext loopback.

`routes.caddy` is seeded with the placeholder content `# no routes` on first boot so it always exists — Caddy's `import` would fail loudly on a missing file.

## Network: nono profile additions

The hermes container runs inside a nono sandbox. Three additions to `hermes-nono-profile.json`:

- **`network.listen_port`**: adds `8788` (technically owned by policy-proxy, but the shared netns means we'd see EADDRINUSE without it being allowlisted), `8789` (WebUI's new home), `9956` (process-compose admin), and `4000–4019` (user webapps). nono profile JSON uses `Vec<u16>`, no range syntax — the 20 ports are listed individually.
- **`network.open_port`**: same set, plus the existing 9901–9903 / 9950–9952 for AWS signer + integrations broker.
- **`filesystem.read_file`**: adds `/opt/doh/bin/{caddy,process-compose,webapps}` and `/opt/doh/runtime/{Caddyfile,webapps}` so the sandbox can exec them.
- **`environment.allow_vars`**: adds `DOH_PUBLIC_HOSTNAME` so the CLI can print real URLs (see below).

## Public hostname injection

The agent prints the URL for the user to click. To do that, it needs to know the public hostname. The container doesn't know it by default — it just sees its own loopback addresses.

**Solution: the CDK extends the env-bearer overlay with `DOH_PUBLIC_HOSTNAME`** (`{subdomain}.{shared_alb_hosted_zone}`) when both are present. Every container with `requires_env_bearer=True` gets it injected at task-definition build time. The nono profile allow-lists the variable so it survives the sandbox env scrub. The `webapps` CLI's `url_for(slug)` reads `DOH_PUBLIC_HOSTNAME`; if missing, it prints `<your-agent-hostname>` as a placeholder. The skill instructs the agent to (i) always render the URL as a clickable markdown link, and (ii) substitute the user-visible hostname from the browser's address bar if the placeholder appears.

## Two known restrictions on what runs inside

**Apps must bind to `127.0.0.1`, not `0.0.0.0`.** Caddy is the only thing that should be reachable from outside the container — apps go through Caddy's reverse_proxy, no shortcut. Many frameworks default to all-interfaces; they need explicit configuration. The skill calls this out in the Don'ts.

**Phoenix needs explicit endpoint binding.** Phoenix's HTTP port is configurable; generated apps usually read `PORT`, so `PORT=$WEBAPP_PORT mix phx.server` is the right dev-server shape when the endpoint is configured to bind loopback. For durable apps, **Phoenix releases are the preferred shape**: `MIX_ENV=prod mix release`, then run the release binary from `webapps create --command` with the same `127.0.0.1:$WEBAPP_PORT` binding. WebSocket support itself is unaffected — Caddy's `reverse_proxy` upgrades transparently, verified end-to-end with a Python `websockets` echo server.

## Lifecycle: cold start

ECS replaces the task. persistent-root-runner restores `/workspace/` from the persistent volume. webui.sh starts inside nono and:

1. Seeds `/workspace/webapps/` if missing (idempotent, only first boot).
2. Starts `process-compose up` against the existing YAML — apps marked enabled come back up automatically; ones marked `disabled: true` stay down (process-compose honors disabled on initial up).
3. Waits for WebUI on 8789 to become healthy.
4. Starts Caddy with `--watch`. Caddy reads the existing `routes.caddy` (left in correct state by the last CLI mutation before shutdown) and routes are live immediately.

`webapps list` after cold start shows everything with the same state it had before, modulo a few seconds of "Pending → Running" while processes initialize.

End-to-end verified on `hermes-vmendi-webapps`: created `persist-test`, killed the task with `restart-task`, replacement task came up, `webapps list` showed `persist-test` Running+Ready automatically, HTTP 200 served at the original URL.

## Out of scope (v1)

- **Non-HTTP background workers.** A Discord bot, a cron job, a queue consumer. Same supervisor would manage them but with no port + no Caddy route. `webapps create --no-port` was considered and rejected — the name `webapps` is a contract, and "background services" is a separate concept worth its own primitive in v2.
- **WebUI sidebar discoverability.** No "Web Apps" panel in the WebUI listing live apps. The agent telling the user the URL after `webapps create` is sufficient for v1; a UI integration is a `webui-extension/doh.js` change for later.
- **Resource limits per app.** A user app eating 100% CPU starves Hermes. process-compose doesn't do cgroup limits; ECS task-level limits exist but per-app limits don't. Not fixed in v1.
- **Trash-bin on delete.** `webapps delete --yes` is total: route, supervision, logs, AND `projects/<slug>/`. Considered moving to `.trash/` for recoverability but rejected — too much janitorial complexity for a low-frequency operation. The skill mandates explicit user confirmation before passing `--yes`.

## Files of interest

- **`template_repos/hermes_agent/doh_runtime/Caddyfile`** — the static config Caddy loads at boot.
- **`template_repos/hermes_agent/doh_runtime/webapps`** — the CLI. Single Python file.
- **`template_repos/hermes_agent/doh_runtime/webui.sh`** — launches Caddy + process-compose + WebUI inside nono and propagates failures.
- **`template_repos/hermes_agent/doh_runtime/supervisor.sh`** — exports `HERMES_WEBUI_PORT=8789` so WebUI clears port 8787 for Caddy.
- **`template_repos/hermes_agent/doh_runtime/hermes-nono-profile.json`** — port allow-lists, binary read-allows, `DOH_PUBLIC_HOSTNAME` allow_vars entry.
- **`template_repos/hermes_agent/Dockerfile`** — downloads Caddy + process-compose binaries; symlinks the CLI onto PATH.
- **`template_repos/hermes_agent/skills/webapps/SKILL.md`** — agent-facing contract, worked example, per-framework `X-Forwarded-Prefix` notes, Don'ts.
- **`devopshero_app/services/infra_customer/deploy_app.py`** — injects `DOH_PUBLIC_HOSTNAME` into the env-bearer overlay (around line 783).
