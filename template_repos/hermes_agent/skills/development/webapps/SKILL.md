---
name: webapps
description: Build, run, and serve user web apps. Use when the user asks for a web, a web app, dashboard, site, or HTTP service that should be reachable from outside the agent.

version: 2.0.0
license: MIT
metadata:
  hermes:
    tags: [WebApps, Hosting, ReverseProxy, Caddy, ProcessCompose]
---

# Webapps

The user will be able to reach the app in a browser at a subdomain of the agent's hostname.

## What this gives you

A `webapps` CLI on PATH. It registers a process (any language: Python, Node, Elixir, Go, Rust, …) with a supervisor and adds a same-task reverse-proxy route so the user can reach the app at `https://<slug>.<agent-hostname>/`.

When the user asks you to create/clone the web app from a Git repository, clone it directly inside /workspace/webapps/projects/

## Mental model

- Each webapp gets its own subdomain: `<slug>.<agent-hostname>`. The agent's wildcard DNS + wildcard TLS cert make any new slug reachable instantly — no DNS work, no per-app infra.
- The app's view of the world is simple: a request to `https://<slug>.<agent-hostname>/foo/bar` arrives at the app as `GET /foo/bar`. **There is no path prefix.** Absolute paths in HTML, JS, and WebSocket URLs (`/assets/...`, `/api/...`, `/ws`) work without configuration.
- The app must bind to `127.0.0.1:$WEBAPP_PORT` (the CLI picks the port). Caddy fronts it; nothing else should be reachable.

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

- **`<slug>`**: lowercase, 2–32 chars, letters/digits/hyphens. Must start with a letter, end alphanumeric. Becomes the leftmost DNS label, so all DNS-safe constraints apply.
- **`--command`**: full shell command. Use `$WEBAPP_PORT` to read the port — the CLI sets that env var automatically.
- **`--cwd`**: absolute path to the project working directory (typically `/workspace/webapps/projects/<slug>`).
- **`--timeout`**: seconds to wait for readiness (default 90; bump for slow first-compile stacks like Phoenix).

## Dependencies

Before `webapps create`, install whatever runtime dependencies the project needs — if it has a manifest or install step, use that (`requirements.txt` or `pyproject.toml` for Python, `package.json` for Node, `mix.exs` / `mix deps.get` for Elixir, and so on). README "Run" sections are often incomplete; check the source if anything looks missing. Do not register the app until dependencies are installed and a quick smoke test passes (e.g. the start command runs without an immediate import/build error).

## Worked example: a static site

```bash
mkdir -p /workspace/webapps/projects/hello
cat > /workspace/webapps/projects/hello/index.html <<'EOF'
<!doctype html><h1>Hello from hello.<agent-host></h1>
EOF

webapps create hello \
    --command 'python3 -m http.server "$WEBAPP_PORT" --bind 127.0.0.1' \
    --cwd /workspace/webapps/projects/hello
```

The CLI prints a final line like `webapps: 'hello' is live at https://hello.<agent-host>/ (port 4000)`. **Always show that URL to the user as a clickable markdown link**, e.g.:

> Your app is live at [https://hello.hermes-foo.example.com/](https://hello.hermes-foo.example.com/)

Use the *exact host* from the CLI's output, with the trailing slash.

## Lifecycle patterns

- **Create:** scaffold under `/workspace/webapps/projects/<slug>/`, install dependencies if the app needs them, then `webapps create` with `$WEBAPP_PORT` in the command. Verify with `webapps list` (must be `Ready`) and `webapps logs` before reporting the URL.
- **Fix and redeploy after a crash:** `webapps logs <slug>`, fix the app (including missing deps), `webapps restart <slug>`.
- **Change env:** `webapps set-env <slug> KEY=VALUE`.
- **Take down (reversible):** `webapps stop <slug>`. Source under `projects/<slug>/` is preserved.
- **Delete (total):** confirm with the user that source code AND logs will be removed, then `webapps delete <slug> --yes`.

## Framework gotchas

- **Phoenix / Elixir** — read [`elixir.md`](elixir.md) before scaffolding (sandbox bind constraints).
- **ttyd** — read [`ttyd.md`](ttyd.md) before registering a web terminal.

## Don'ts

- **Don't edit `/workspace/.config/process-compose/process-compose.yaml` or `/workspace/.config/caddy/routes.caddy` by hand.** Use the CLI. Hand-edits will be clobbered.
- **Don't pick a port manually.** Use `$WEBAPP_PORT` in `--command`. The CLI assigns ports.
- **Don't claim success without verifying.** Run `webapps list` after `create`/`start` to confirm the app is `Ready`. If readiness times out, read the logs.
- **Don't run `webapps delete <slug> --yes` without first telling the user what will be removed and getting explicit confirmation.** Delete is total: route, supervision, logs, AND `projects/<slug>/`.
- **Don't bind the app to anything other than `127.0.0.1:$WEBAPP_PORT`.** Apps must listen on loopback only. Many frameworks default to `0.0.0.0`; explicitly bind to `127.0.0.1`.
- **Don't put your project source elsewhere.** Keep code under `/workspace/webapps/projects/<slug>/`. The CLI's `delete` cleans that path; if your code is somewhere else, deletion will leave orphans.
- **Don't create or delete slugs starting with `__`.** They're reserved for platform internals Pick a slug that begins with a letter.
- **Don't plan nor offer to build an authentication feature to the user for the web app.** The app does not need any authentication mechanism because the platform provides for it through a policy proxy.

## Failure modes

- **`EACCES` / "permission denied" on bind**: the app is trying to bind to a port outside the allowed range (4000–4019). Configure the app to bind only to `$WEBAPP_PORT`.


## Calling the Hermes agent from a webapp

A webapp can call a full Hermes agent over a loopback API: The gateway exposes an OpenAI-compatible server at `http://127.0.0.1:8642/v1/...`:

- `POST /v1/chat/completions`, `POST /v1/responses` — OpenAI-compatible chat
- `POST /v1/runs` — start an agent run (returns a `run_id`); `GET /v1/runs/{id}/events` to stream
- `GET /v1/models`, `GET /v1/capabilities`

Authenticate with a bearer token read from the `API_SERVER_KEY` env var, which every webapp inherits automatically:

```python
import os, httpx
resp = httpx.post(
    "http://127.0.0.1:8642/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['API_SERVER_KEY']}"},
    json={"messages": [{"role": "user", "content": "..."}]},
)
```

The `model` field is optional — if omitted the gateway uses its active agent profile. To advertise a specific model name, `GET /v1/models` first and use the `id` it returns.

This endpoint is loopback-only inside the sandbox — never expose it through your webapp to the public route.
