---
name: widgets
description: Create, modify, debug, verify, and delete embedded HumR Widgets shown inside Hermes WebUI. Use for user requests to build an embedded app, dashboard, tracker, interactive panel, or mini-application that belongs in the Widgets panel; use Web Apps instead when the application needs its own hostname or a standalone browser experience.
---

# Widgets

Build Widgets under `/workspace/widgets/<slug>/` and manage them with the `widgets` CLI on `PATH`. A Widget appears in the WebUI's Widgets panel at `/widgets/<slug>/`; it is distinct from a Web App at its own hostname.

## Choose the runtime shape

Default to the simplest shape that meets the request:

1. Use a static frontend when all behavior can run in the browser.
2. Add a backend when the Widget needs secrets, filesystem access, integrations, databases, AWS, MCP, or the Hermes API.
3. Use a backend-served frontend only when a framework needs server-side rendering or owns the full HTTP stack.

Every Widget has a frontend. Do not create authentication inside it: the HumR policy proxy protects the parent agent host.

## Follow the directory and manifest contract

Use a lowercase slug of 2–32 letters, digits, or hyphens. Start with a letter, end with a letter or digit, and never use a slug beginning with `__`.

Create exactly one authoritative manifest at `/workspace/widgets/<slug>/widget.json`. Do not put the slug, port, status, ordering, appearance, enablement, or capabilities in the manifest.

For a static Widget:

```json
{
  "schema_version": 1,
  "title": "Customer Dashboard",
  "icon": "chart-bar",
  "frontend": {
    "mode": "static",
    "entry": "index.html"
  }
}
```

For a static frontend with an API backend:

```json
{
  "schema_version": 1,
  "title": "Customer Dashboard",
  "icon": "chart-bar",
  "frontend": {
    "mode": "static",
    "entry": "index.html"
  },
  "backend": {
    "command": "uv run backend/server.py"
  }
}
```

For a backend-served frontend:

```json
{
  "schema_version": 1,
  "title": "Customer Dashboard",
  "frontend": {
    "mode": "backend"
  },
  "backend": {
    "command": "uv run server.py"
  }
}
```

`title` is required. `icon` is optional; omit it for the generic Widget icon. Supported host icons are `widget`, `chart-bar`, `calendar`, `code`, `globe`, `table`, and `list-check`.

For static mode, keep `frontend.entry` a canonical relative path to a regular file inside the Widget directory. Put all other frontend files beneath that directory; paths escaping it are rejected.

## Build for the mounted path

Use relative browser URLs so the Widget remains under its assigned prefix:

```js
fetch('./api/customers');
```

Use `./assets/app.js`, not `/assets/app.js`. Do not hard-code the agent hostname or a backend port.

For a static frontend with a backend, requests under `/widgets/<slug>/api/*` reach the backend as `/api/*`. For backend-served mode, the entire `/widgets/<slug>` prefix is stripped before proxying.

## Match the WebUI by default

Give the Widget its own scoped HTML, CSS, and JavaScript. Do not import the WebUI's full stylesheet.

Unless the user requests an independent visual system, read the computed CSS variables from the same-origin parent document and copy the needed values onto the Widget document. Observe `class`, `data-skin`, and `style` changes on `window.parent.document.documentElement` so theme changes stay synchronized. Prefer the WebUI variables already used by the shell, including `--bg`, `--surface`, `--surface2`, `--text`, `--text2`, `--muted`, `--border`, `--accent`, and `--code-bg`.

If the user explicitly requests a standalone theme or framework visual system, preserve it instead.

## Add a backend safely

Run the backend from the Widget directory and bind exactly to `127.0.0.1:$WIDGET_PORT`. Read these runtime variables rather than choosing values:

- `WIDGET_PORT`: assigned loopback port.
- `WIDGET_SLUG`: Widget slug.
- `WIDGET_BASE_PATH`: `/widgets/<slug>`.

Keep secrets and privileged calls in the backend, never in browser code. Backend processes inherit the sandbox's permitted integrations and configuration.

To call Hermes, use the loopback OpenAI-compatible API at `http://127.0.0.1:8642/v1` and authenticate with `Authorization: Bearer $API_SERVER_KEY`. Never forward `API_SERVER_KEY` to the frontend. The MCP aggregator is available at `http://127.0.0.1:9952/mcp`.

## Apply and verify

Use this workflow for both new Widgets and modifications:

1. Inspect any existing Widget directory before changing it.
2. Create or update the source and `widget.json` in place.
3. Install or build dependencies before applying when the chosen stack requires it.
4. Run `widgets apply <slug>`.
5. Run `widgets list` and confirm the Widget is listed.
6. Verify the frontend through Caddy:

```bash
curl -fsS \
  -H "X-Forwarded-Host: $HUMR_PUBLIC_HOSTNAME" \
  "http://127.0.0.1:8787/widgets/<slug>/"
```

7. Exercise at least one meaningful interaction or API path. If a backend does not become ready, run `widgets logs <slug>` and fix the cause before reporting success.

`widgets apply <slug>` validates the manifest, regenerates the registry and routes, reconciles the backend when present, and waits for a targeted backend to become ready. Do not edit files under `/workspace/.config/widgets/`, `/workspace/.config/caddy/`, or `/workspace/.config/process-compose/` by hand.

## Delete only with explicit confirmation

`widgets delete` permanently removes the Widget source, logs, route, registry entry, and supervisor state. When the user asks to delete a Widget:

1. State that source and logs will be permanently removed.
2. Obtain explicit confirmation.
3. Run `widgets delete <slug> --yes`.

Do not run deletion speculatively or as a repair step.
