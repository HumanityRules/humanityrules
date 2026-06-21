# Gateway Env Vars & Targeted Restart

How vault-style integration credentials whose env presence activates a
gateway platform binding (today: Telegram; future: other messaging
platforms) reach the in-sandbox Hermes gateway, and how the gateway is
restarted in place when those credentials change — without redeploying
the container.

## The activation contract

The Hermes gateway is the long-running process inside the nono sandbox
that handles messaging-platform bindings (Telegram, Discord, Slack, …)
and the cron ticker. For any platform, **presence of the platform's
token env var at gateway startup activates that platform**. Hermes reads
those env vars from `${HERMES_HOME}/.env` via its existing
`get_env_value` helper.

The token's *content* doesn't matter on the wire to api.telegram.org:
the broker's TLS-intercept proxy rewrites the placeholder
`000000:HUMR_PLACEHOLDER` → real token on every request. The gateway
sees the placeholder forever; only its *presence* matters.

## The end result

The integrations broker is the single owner of `${HERMES_HOME}/.env`'s
managed block. It writes that block on startup and on every credential
change, then asks process-compose to restart the gateway in place when
the change requires it.

```
HERMES_DEBUG=1                                    ← user/onboarding lines preserved

# === HUMR-MANAGED-INTEGRATIONS BEGIN ===
TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER
TELEGRAM_ALLOWED_USERS=1,2,3
# === HUMR-MANAGED-INTEGRATIONS END ===
```

The broker rewrites only the lines between sentinels; everything outside
the block survives untouched. Atomic via tempfile + `os.replace()`.
Empty managed block (no vault providers connected) is fine.

### Spec carries the env mapping

Each `TlsProviderSpec` declares its env bindings; the broker projects
connected `_token_store` cache → managed block by following those bindings.
Telegram:

```python
credential_method=VaultUrlRewrite(
    placeholder="000000:HUMR_PLACEHOLDER",
),
env_bindings=(
    EnvBinding(env_var="TELEGRAM_BOT_TOKEN", value="000000:HUMR_PLACEHOLDER"),
    EnvBinding(env_var="TELEGRAM_ALLOWED_USERS", config_key="allowed_users", list_separator=","),
)
```

Adding a future messaging platform that gates activation on env presence
is one `TlsProviderSpec` entry — no shell, no supervisor changes.
URL-rewrite providers used only by agent tools (e.g., GitHub) don't
need env bindings: the broker's TLS interception is enough on
its own.

### One render path, two triggers

The same `_render_gateway_env_file()` runs:

1. **On broker startup**, after `_ensure_fresh` for every provider.
   Supervisor's `wait_for_port` on the broker control port doubles as
   the synchronization point: by the time it returns, the file is on
   disk and the gateway can boot.
2. **On every `_token_store.invalidate(slug)`** — the chokepoint that
   covers vault save, vault disconnect, and explicit "Refresh all".

Pure projection of cache → managed-block string. No "first call vs
subsequent" branch.

### Targeted restart

`TlsProviderSpec.restart_gateway_after_save` is load-bearing. After the
broker rewrites `.env`, if the connected/disconnected provider's spec
demands a gateway restart, the broker POSTs to process-compose:

```
POST http://127.0.0.1:9956/process/restart/system.gateway
```

process-compose stops the gateway and respawns it. The fresh process
inherits the `.env` via Hermes's normal path. WebUI and agent are
untouched — open chat sessions don't drop.

Providers that do not affect gateway startup keep
`restart_gateway_after_save = False`. The same trigger covers connect
*and* disconnect: connect populates the managed block, disconnect empties
it; the gateway either picks up the platform binding or boots without it.

### Process supervision: split system and webapps projects

HUMR-supervised processes inside nono are split across two
process-compose daemons:

1. `127.0.0.1:9956` reads
   `/workspace/.config/process-compose/system/process-compose.yaml`
   and supervises `system.gateway` plus `system.webui`.
2. `127.0.0.1:9957` reads
   `/workspace/.config/process-compose/webapps/process-compose.yaml`
   and supervises `__admin` plus user webapps.

Generated Caddy routes live at
`/workspace/.config/caddy/routes.caddy` and are derived only from the
webapps YAML.

Naming:

- `__<slug>` — platform-internal webapps (today: `__admin`).
- `system.<slug>` — HUMR-managed system processes (today:
  `system.gateway`).
- Everything else — user webapps.

The `webapps` CLI rejects creating slugs starting with `system.` and
talks only to the webapps daemon. System entries are seeded by
`webui.sh` directly via `system_process_compose_seed.py`.

### Failure surface

Broker writes `.env` successfully but the process-compose REST call
fails (process-compose down, transient network glitch). The vault
Save's success response chains through invalidate → broker write →
restart-call. If the restart call fails, the broker returns an error;
the modal surfaces "Saved, but the gateway restart failed. Redeploy
this Hermes app to apply the new credentials." A real fault worth
seeing.

## Boot ordering

```
supervisor (root, outside nono):
  start aws_signer
  start integrations_broker
    ├─ broker fetches HUMR state for all providers
    ├─ broker writes ${HERMES_HOME}/.env managed block
    └─ broker opens control port  ← supervisor's wait_for_port unblocks
  launch nono → webui.sh

webui.sh (hermeswebui, inside nono):
  bootstrap_admin_webapp        (creates webapps layout, writes __admin)
  bootstrap_gateway_process     (creates system layout, writes system.gateway)
  bootstrap_webui_process       (writes system.webui into the system YAML)
  start system process-compose  (brings up system.gateway + system.webui)
  start webapps process-compose (brings up __admin + user webapps)
  wait_for_webui
  start_caddy
```

## Why not …

- **Why not write `.env` from `webui.sh`?** webui.sh runs only at full
  container boot. Connect/disconnect events between boots wouldn't
  trigger a re-render, defeating the targeted-restart goal.
- **Why not pass env directly to the gateway process at restart time
  (no file)?** Other Hermes CLI tools (`hermes status`, `hermes tools`,
  `hermes setup`) read `${HERMES_HOME}/.env` via `get_env_value`. If
  the file disagrees with the gateway's actual state, an agent invoking
  those tools sees a contradictory answer. The .env file is a debugging
  surface; keeping it consistent prevents the agent and the user from
  drifting into confused states.
- **Why not have the broker write the file but supervisor send the
  restart signal?** Two writers to the restart trigger, ordering
  ambiguity. The broker is the only component that observes the *event*
  in real time; signaling from anywhere else means polling.
- **Why split process-compose instances (system + webapps)?**
  `process-compose project update` applies at daemon/project scope. If
  webapp CRUD shares a project with `system.webui`, registering a webapp
  can restart WebUI and interrupt an active chat stream. Splitting the
  projects keeps the existing simple YAML + `project update` workflow for
  webapps while constraining its blast radius to `__admin` and user apps.
