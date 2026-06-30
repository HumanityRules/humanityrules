# Phoenix / Elixir on webapps

Elixir and Erlang/OTP are preinstalled and on PATH (`elixir`, `mix`, `iex`, `erl`) — don't install them. `mix`, `mix release`, and release startup work as-is, no special flags. (Ignore the harmless `failed to subscribe to Mix events` warning.)

**Bind your HTTP server to `127.0.0.1:$WEBAPP_PORT`** — never `0.0.0.0` or a fixed port. Phoenix honors `PORT`, so bind the endpoint to loopback and run with `PORT=$WEBAPP_PORT`.

## Run a release (preferred for durable apps)

```bash
cd /workspace/webapps/projects/<slug>
MIX_ENV=prod mix release --overwrite
webapps create <slug> --command '_build/prod/rel/<app>/bin/<app> start' --cwd /workspace/webapps/projects/<slug>
webapps start <slug>
```

Quick dev server instead: `PORT=$WEBAPP_PORT mix phx.server`.

## Redeploy after changes

```bash
cd /workspace/webapps/projects/<slug>
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release --overwrite
webapps restart <slug>
```
