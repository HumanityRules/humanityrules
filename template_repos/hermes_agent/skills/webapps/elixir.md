# Phoenix / Elixir on webapps

Read this *before* scaffolding any Elixir or Phoenix app under `webapps`. The sandbox has four traps that fire on every Mix 1.19+ project and on every Erlang release; all four have fixes you must apply up front.

## Endpoint config

Configure the endpoint to listen on `127.0.0.1:$WEBAPP_PORT` (generated apps usually honor `PORT`, so run with `PORT=$WEBAPP_PORT`). For anything beyond a quick dev server, build a release with `MIX_ENV=prod mix release` and run the release binary from `webapps create --command`.

## Sandbox bind restrictions — the four fixes

The sandbox blocks `bind(127.0.0.1, <ephemeral>)` outside the 4000–4019 webapps range. That breaks:

### 1. `Mix.Sync.Lock` (Mix 1.19+)

Opens a loopback socket on a random port for the build-time concurrency lock. Disable it for every `mix` invocation:

```bash
export MIX_OS_CONCURRENCY_LOCK=0
```

### 2. `Mix.PubSub` (Mix 1.19+)

Same problem, no env-var off-switch. Replace its compiled BEAM with a no-op once after `brew install elixir`:

```bash
pubsub=/home/linuxbrew/.linuxbrew/opt/elixir/lib/elixir/lib/mix/ebin/Elixir.Mix.PubSub.beam
[ -f "$pubsub.orig" ] || cp "$pubsub" "$pubsub.orig"
```

Then write an Elixir source file `defmodule Mix.PubSub` that stubs every public function `Mix.PubSub` exposes (inspect the original module's exports first — `:beam_lib.chunks(~c"#{pubsub}.orig", [:exports])` from `iex`), `elixirc` it, and copy the resulting `Elixir.Mix.PubSub.beam` over the install path. Re-apply after every `brew upgrade elixir` — the upgrade restores the original BEAM.

### 3. EPMD / Erlang distribution

Releases start in distributed mode and EPMD wants an ephemeral port → `Protocol 'inet_tcp': register/listen error: eacces`. In the launcher script (`run.sh`) that you reference from `webapps create --command`, export:

```bash
export RELEASE_DISTRIBUTION=none
export ERL_EPMD_PORT=-1
```

### 4. `URL_PATH_PREFIX` for LiveView, sockets, and assets

Phoenix's `X-Forwarded-Prefix` handling doesn't reach LiveView, the WebSocket endpoint, or static asset URLs. Wire the prefix through explicitly in `config/runtime.exs`:

```elixir
prefix = System.get_env("URL_PATH_PREFIX", "")
config :my_app, MyAppWeb.Endpoint,
  url: [host: System.get_env("PHX_HOST", "localhost"), port: 443, scheme: "https", path: prefix],
  static_url: [path: prefix <> "/"]
```

Then `webapps set-env <slug> URL_PATH_PREFIX=/webapps/<slug>` after `webapps create`.

The runtime.exs config above only fixes server-rendered URLs (sigil_p, static assets, redirects). It does **not** rewrite the LiveSocket URL constructed in JS — `assets/js/app.js` has a literal `new LiveSocket("/live", ...)`. That literal hits `/live/websocket` (no prefix), Caddy falls through to WebUI:8789, you get a 404 / "unhandled poll status undefined" in the browser console. Inject the prefix via a meta tag and read it in JS:

```heex
<%!-- lib/my_app_web/components/layouts/root.html.heex, in <head> --%>
<meta name="ws-path" content={MyAppWeb.Endpoint.path("/live")} />
```

```js
// assets/js/app.js
const wsPath = document.querySelector("meta[name='ws-path']")?.getAttribute("content") || "/live"
const liveSocket = new LiveSocket(wsPath, Socket, { /* ... */ })
```

Rebuild assets (`mix assets.deploy`) and the release before restarting.

## Symptom: empty `routes.caddy` after first `webapps create`

The first `webapps create` for an Elixir app will time out on readiness if you skip fix #3 above — `routes.caddy` will stay empty. After fixing the env, `webapps stop && webapps start` regenerates the route block.

## Redeploy after code changes

```bash
cd /workspace/webapps/projects/<slug>
export MIX_OS_CONCURRENCY_LOCK=0
export ELIXIR_ERL_OPTIONS="+fnu"   # add if you hit filename-encoding warnings
MIX_ENV=prod mix assets.deploy
MIX_ENV=prod mix release --overwrite
webapps restart <slug>
```

`--overwrite` is required on rebuilds; without it, `mix release` refuses to overwrite the existing release directory.
