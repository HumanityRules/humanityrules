# ttyd on webapps

Read this *before* registering a `ttyd` web terminal under `webapps`. ttyd has two flag traps that trip every LLM scaffold.

## Recommended command

```bash
webapps create term \
    --command 'ttyd -W --port "$WEBAPP_PORT" --interface 127.0.0.1 bash'
```

## The two flag traps

### 1. `-W` is mandatory for a writable terminal

Without `-W`, ttyd serves the terminal in **read-only** mode: the page renders, output streams correctly, but keystrokes are silently ignored. The symptom is "the terminal loads but I can't type" — confusing because nothing errors. Always pass `-W` unless you explicitly want a view-only terminal.

### 2. **Never** pass `--base-path /webapps/<slug>`

This is a specific case of the general "inbound base-path" rule in `SKILL.md`, but ttyd makes it especially easy to get wrong because the obvious-looking flag exists.

The reverse proxy *already* strips `/webapps/<slug>` from incoming requests before they reach ttyd. If you set `--base-path /webapps/<slug>`, ttyd will only accept incoming requests under that prefix — but by the time the request reaches ttyd, the prefix is already gone, and ttyd returns 404. The app appears `Ready` in `webapps list` but the browser shows 404.

ttyd's client-side JS uses **relative** paths for the WebSocket, so it inherits `/webapps/<slug>/` from the page URL automatically — no outbound URL configuration is needed either.
