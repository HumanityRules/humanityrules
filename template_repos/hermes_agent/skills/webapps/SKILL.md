---
name: webapps
description: Build, run, and serve user web apps. Use when the user asks for a web app, dashboard, site, or HTTP service that should be reachable from outside the agent.

version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [WebApps, Hosting, ReverseProxy, Caddy, ProcessCompose]
---

# Webapps

Use this skill when the user asks you to build a web app, dashboard, microsite, or HTTP service. The user will be able to reach the app in a browser at the agent's own hostname. 

## What this gives you

A `webapps` CLI on PATH. It registers a process (any language: Python, Node, Elixir, Go, Rust, …) with a supervisor and adds a same-host reverse-proxy route so the user can reach the app at `https://<agent-hostname>/webapps/<slug>/`.

## Mental model

- The agent's hostname (where this WebUI lives) is the only public surface.
- Path prefix `/webapps/` is **reserved**. Apps live under `/webapps/<slug>/`.
- The reverse proxy strips `/webapps/<slug>` from the request path and injects `X-Forwarded-Prefix: /webapps/<slug>` so the framework can generate correct URLs back.

## The directory contract

```
/workspace/webapps/
  projects/<slug>/                              # YOUR app source goes here
  logs/<slug>.log                               # captured stdout/stderr

/workspace/.config/process-compose/
  process-compose.yaml                          # supervisor state — DO NOT EDIT BY HAND

/workspace/.config/caddy/
  routes.caddy                                  # generated routes — DO NOT EDIT BY HAND
```

## CLI reference

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

- **`<slug>`**: lowercase, 2–32 chars, letters/digits/hyphens. Must start with a letter, end alphanumeric.
- **`--command`**: full shell command. Use `$WEBAPP_PORT` to read the port — the CLI sets that env var automatically.
- **`--cwd`**: absolute path to the project working directory (typically `/workspace/webapps/projects/<slug>`).
- **`--timeout`**: seconds to wait for readiness (default 90; bump for slow first-compile stacks like Phoenix).

## Worked example: a static site

```bash
mkdir -p /workspace/webapps/projects/hello
cat > /workspace/webapps/projects/hello/index.html <<'EOF'
<!doctype html><h1>Hello from /webapps/hello/</h1>
EOF

webapps create hello \
    --command 'python3 -m http.server "$WEBAPP_PORT"' \
    --cwd /workspace/webapps/projects/hello
```

The CLI prints a final line like `webapps: 'hello' is live at https://<host>/webapps/hello/ (port 4000)`. **Always show that URL to the user as a clickable markdown link**, e.g.:

> Your app is live at [https://hermes-foo.example.com/webapps/hello/](https://hermes-foo.example.com/webapps/hello/)

Use the *exact host* from the CLI's output, with the trailing slash.

## Path-prefix configuration

The reverse proxy strips `/webapps/<slug>` from the request path and sets `X-Forwarded-Prefix: /webapps/<slug>` on the forwarded request. Configure whatever framework you scaffold to honor that prefix when generating absolute URLs and redirects (search the framework's docs for "reverse proxy subpath" or "X-Forwarded-Prefix" if you're unsure).

Static files served by `python -m http.server` need no configuration — relative paths just work.

## Lifecycle patterns

The CLI maps to the shapes you'll need:

- **Create:** scaffold under `/workspace/webapps/projects/<slug>/`, then `webapps create` with `$WEBAPP_PORT` in the command. Verify with `webapps list` (must be `Ready`) and `webapps logs` before reporting the URL.
- **Fix and redeploy after a crash:** `webapps logs <slug>`, edit, `webapps restart <slug>`.
- **Change env:** `webapps set-env <slug> KEY=VALUE`.
- **Take down (reversible):** `webapps stop <slug>`. Source under `projects/<slug>/` is preserved.
- **Delete (total):** confirm with the user that source code AND logs will be removed, then `webapps delete <slug> --yes`.

## Framework gotchas

- **Phoenix / Elixir** — read [`elixir.md`](elixir.md) before scaffolding.
- **ttyd** — read [`ttyd.md`](ttyd.md) before registering a web terminal.
- **Inbound base-path / mount-prefix flags** (router-mount options, `--base-path`, etc.): leave them at the default. The proxy already strips `/webapps/<slug>`, so the app must serve incoming requests at `/`.
- **Outbound public URL settings** (`--root-url`, generated asset URLs, server-rendered WebSocket URLs): these may need the public prefix. Prefer reading `X-Forwarded-Prefix`; if the framework cannot, pass `/webapps/<slug>` as a public URL/path setting only — never as an inbound router mount.
- **Client-side WebSocket URLs**: if the framework's JS opens a WebSocket using an *absolute* path (e.g. `new WebSocket("/ws")`, `new LiveSocket("/live", …)`, Socket.IO defaults, Vite HMR), the connection bypasses `/webapps/<slug>` and the proxy returns 404. Either configure the framework to emit a prefixed path, or inject the prefix into the page (meta tag, data attribute, …) and read it in JS. Frameworks that use *relative* paths (ttyd) need no change.

## Don'ts

- **Don't edit `/workspace/.config/process-compose/process-compose.yaml` or `/workspace/.config/caddy/routes.caddy` by hand.** Use the CLI. Hand-edits will be clobbered.
- **Don't pick a port manually.** Use `$WEBAPP_PORT` in `--command`. The CLI assigns ports.
- **Don't claim success without verifying.** Run `webapps list` after `create`/`start` to confirm the app is `Ready`. If readiness times out, read the logs.
- **Don't run `webapps delete <slug> --yes` without first telling the user what will be removed and getting explicit confirmation.** Delete is total: route, supervision, logs, AND `projects/<slug>/`.
- **Don't bind the app to anything other than `127.0.0.1:$WEBAPP_PORT`.** Apps must listen on loopback only. Many frameworks default to `0.0.0.0`; explicitly bind to `127.0.0.1`.
- **Don't put your project source elsewhere.** Keep code under `/workspace/webapps/projects/<slug>/`. The CLI's `delete` cleans that path; if your code is somewhere else, deletion will leave orphans.
- **Don't create or delete slugs starting with `__`.** They're reserved for platform internals (e.g. `__admin`). Pick a slug that begins with a letter.
- **Don't plan nor offer to build an authentication feature to the user for the web app.** The app does not need any authentication mechanism because the platform provides for it through a policy proxy.
  
## Failure modes

- **"did not become ready"** after `webapps create`: check `webapps logs <slug>`. The app probably crashed at startup, didn't bind the port, or bound the wrong port (must use `$WEBAPP_PORT`).
- **502 Bad Gateway in browser**: the app crashed after registering. `webapps list` will show non-`Ready`. Logs have the trace.
- **404 Not Found in browser**: either the slug is wrong or the app is stopped. `webapps list` shows current routes.
- **`EACCES` / "permission denied" on bind**: the app is trying to bind to a port outside the allowed range (4000–4019). Configure the app to bind only to `$WEBAPP_PORT`.
- **404 in browser but `webapps list` shows `Ready`**: the app is probably expecting incoming requests under `/webapps/<slug>`. Remove the inbound base-path/mount setting; keep only outbound public URL prefix config if the framework needs it.
