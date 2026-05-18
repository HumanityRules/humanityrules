---
name: webapps
description: Build, run, and serve user web apps from the Hermes agent at the same hostname (https://<agent>/webapps/<slug>/). Use when the user asks for a web app, dashboard, site, or HTTP service that should be reachable from outside the agent.

version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [WebApps, Hosting, ReverseProxy, Caddy, ProcessCompose]
---

# Webapps

Use this skill when the user asks you to build a web app, dashboard, microsite, or HTTP service that they can reach in a browser at the agent's own hostname.

## What this gives you

A `webapps` CLI on PATH. It registers a process (any language: Python, Node, Elixir, Go, Rust, …) with a supervisor and adds a same-host reverse-proxy route so the user can reach the app at `https://<agent-hostname>/webapps/<slug>/`. The agent never needs DNS, ports, ALB rules, or extra hostnames.

## Mental model

- The agent's hostname (where this WebUI lives) is the only public surface.
- Path prefix `/webapps/` is **reserved**. Apps live under `/webapps/<slug>/`.
- Each app picks a free port in the 4000–4019 range; the CLI handles selection automatically.
- The reverse proxy strips `/webapps/<slug>` from the request path and injects `X-Forwarded-Prefix: /webapps/<slug>` so the framework can generate correct URLs back.

## The directory contract

Source of truth lives in `/workspace/webapps/`:

```
/workspace/webapps/
  process-compose.yaml       # supervisor state — DO NOT EDIT BY HAND
  caddy/<slug>.caddy         # routing snippet — DO NOT EDIT BY HAND
  projects/<slug>/           # YOUR app source goes here
  logs/<slug>.log            # captured stdout/stderr
```

Use the `webapps` CLI for all mutations. **Never edit `process-compose.yaml` or `caddy/<slug>.caddy` by hand.**

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
webapps next-port
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

Use the *exact host* from the CLI's output. Do not invent a hostname, do not write the literal string `<your-agent-hostname>`, and do not omit the trailing slash. If the CLI prints `<your-agent-hostname>` it means `DOH_PUBLIC_HOSTNAME` wasn't injected — read the URL from the user's browser address bar (it's the same hostname they used to reach you) instead of repeating the placeholder.

## Per-framework path-prefix configuration

The reverse proxy strips the `/webapps/<slug>` prefix and forwards the bare path. Most frameworks need a hint to generate correct absolute URLs in HTML/redirects. Pick the right snippet for the framework you scaffolded:

- **Phoenix (Elixir)**: in `config/config.exs`, set `url: [path: "/webapps/<slug>", host: "..."]` on the endpoint. Phoenix LiveView WebSockets work through the proxy automatically.
- **Express (Node)**: `app.set("trust proxy", true)`; build URLs with `req.headers["x-forwarded-prefix"]`.
- **Rails**: in `config/application.rb`, `config.relative_url_root = "/webapps/<slug>"`.
- **FastAPI / Starlette (Python)**: pass `root_path="/webapps/<slug>"` to the app, or run uvicorn with `--root-path /webapps/<slug>`.
- **Flask**: set `APPLICATION_ROOT = "/webapps/<slug>"` and use `werkzeug.middleware.dispatcher.DispatcherMiddleware`, or rely on `X-Forwarded-Prefix` via `werkzeug.middleware.proxy_fix.ProxyFix`.
- **Static `python -m http.server`**: no config needed; relative paths in HTML just work.

If you scaffold a framework not listed, search its docs for "reverse proxy subpath" or "X-Forwarded-Prefix".

## Lifecycle patterns

**User: "Make a Phoenix counter app at /webapps/counter."**
1. Install Elixir/Erlang if missing: `sudo apt-get update && sudo apt-get install -y elixir erlang-dev`. (First-time install can take several minutes — set the user's expectation.)
2. `cd /workspace/webapps/projects && mix phx.new counter --no-ecto`.
3. Edit `config/dev.exs` so the endpoint has `url: [path: "/webapps/counter", host: "..."]` and `http: [ip: {127, 0, 0, 1}, port: System.get_env("WEBAPP_PORT", "4000") |> String.to_integer()]`.
4. **Important:** `mix phx.server` (dev mode) needs `Mix.Sync.PubSub`, which binds a random ephemeral port — that port range is *not* allowed by the sandbox profile. To avoid this, build a release: `MIX_ENV=prod mix release` and run `_build/prod/rel/counter/bin/counter start` instead of `mix phx.server`. This skips Mix.Sync entirely and is closer to how you'd run Phoenix in production anyway.
5. `webapps create counter --command "_build/prod/rel/counter/bin/counter start" --cwd /workspace/webapps/projects/counter --timeout 180`.
6. Tail logs with `webapps logs counter -f` until you see `Running CounterWeb.Endpoint`.
7. Tell the user the URL.

**User: "The app crashed — fix and redeploy."**
1. `webapps logs <slug>` to see the failure.
2. Edit code in `/workspace/webapps/projects/<slug>/`.
3. `webapps restart <slug>`.

**User: "Set my OPENAI_API_KEY on the app."**
1. `webapps set-env <slug> OPENAI_API_KEY=...`. process-compose surgically restarts the affected app only.

**User: "Take down /webapps/foo."**
1. `webapps stop foo` — the URL immediately starts returning 404. Source code in `projects/foo/` is preserved.

**User: "Delete /webapps/foo entirely."**
1. **First, confirm with the user** that they want to delete the app, the source code at `projects/foo/`, and the logs. Be specific about what's being deleted.
2. Only after explicit confirmation: `webapps delete foo --yes`.

## Don'ts

- **Don't edit `/workspace/webapps/process-compose.yaml` or `caddy/*.caddy` by hand.** Use the CLI. Hand-edits will be clobbered.
- **Don't pick a port manually.** Use `$WEBAPP_PORT` in `--command`. The CLI assigns ports.
- **Don't claim success without verifying.** Run `webapps list` after `create`/`start` to confirm the app is `Ready`. If readiness times out, read the logs.
- **Don't run `webapps delete <slug> --yes` without first telling the user what will be removed and getting explicit confirmation.** Delete is total: route, supervision, logs, AND `projects/<slug>/`.
- **Don't bind the app to anything other than `127.0.0.1:$WEBAPP_PORT`.** Apps must listen on loopback only — Caddy is the only thing that should be reachable from outside the container. Many frameworks default to `0.0.0.0`; explicitly bind to `127.0.0.1` or `localhost`.
- **Don't put your project source elsewhere.** Keep code under `/workspace/webapps/projects/<slug>/`. The CLI's `delete` cleans that path; if your code is somewhere else, deletion will leave orphans.
- **Don't echo `<your-agent-hostname>` as if it were a URL.** It's a placeholder the CLI prints when `DOH_PUBLIC_HOSTNAME` isn't injected. If you see it, substitute the user-visible hostname yourself (look at the URL the user used to reach this WebUI). Then present the link as clickable markdown: `[https://.../webapps/<slug>/](https://.../webapps/<slug>/)`.

## Failure modes

- **"did not become ready"** after `webapps create`: check `webapps logs <slug>`. The app probably crashed at startup, didn't bind the port, or bound the wrong port (must use `$WEBAPP_PORT`).
- **502 Bad Gateway in browser**: the app crashed after registering. `webapps list` will show non-`Ready`. Logs have the trace.
- **404 Not Found in browser**: either the slug is wrong or the app is stopped. `webapps list` shows current routes.
- **"no free ports"**: delete an app you don't need, or ask the platform team to widen the port range.
- **`EACCES` / "permission denied" on bind**: the app is trying to bind to a port outside the allowed range (4000–4019). Check the framework — Mix dev tools, Node debuggers, and some auto-port-pickers grab random ephemeral ports. Configure the framework to bind only to `$WEBAPP_PORT`, or run a production build (e.g. `mix release` for Phoenix) that doesn't need Mix.Sync.
