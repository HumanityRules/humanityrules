# WebUI patches

DOH-owned fixes against the `ghcr.io/nesquena/hermes-webui` image tree
(`/apptoo/` inside the container). Applied at **image build time** by the
`Dockerfile`, not at container start, before `/apptoo` is copied into
`$HERMES_WEBUI_DIR`. That keeps the WebUI source, dependency install, and
compiled bytecode in sync with the patched tree.

The Dockerfile uses one generic applier for both patch sets:
`apply-patches.py <patch-dir> <target-dir>`. It calls that script once for
this directory targeting `/apptoo`, and once for `patches/` targeting
`$HERMES_WEBUI_AGENT_DIR` before `uv pip install` builds the venv.

Each patch targets a path relative to `/apptoo/`. The Dockerfile runs
the generic applier for every `*.patch` here, then runs
`python3 -m compileall` over `/apptoo` so fresh `.pyc` stamps with the
patched source's `(mtime, size)` and stale bytecode can't shadow the
patched sources at runtime.

## Patch lifecycle

1. Add `NN-description.patch` to this directory.
2. Document the upstream issue/PR in the patch header when possible — each
   patch should have an expiration date (the day the fix lands upstream).
3. On WebUI version bump, re-verify each patch applies cleanly. A fuzz or
   reject is a build failure, which surfaces drift early.

## Current patches

### `01-provider-model-labels.patch`

**Target:** `api/config.py` (group builder at `get_available_models`).

**Problem:** The config-driven `providers.<pid>.models` dict form in
`config.yaml` is meant to let deployments ship model lists per provider
with pretty labels. But the code discards the dict values and sets
`"label": k` (the ID) regardless. Net effect: dropdown shows raw model
IDs like `us.anthropic.claude-opus-4-7` instead of "Claude Opus 4.7".

**Fix:** When `cfg_models` is a dict, use the value as the label, falling
back to the key when the value is empty/non-string.

### `02-quiet-200-access-logs.patch`

**Target:** `server.py` (`Handler.log_request`).

**Problem:** The WebUI's request handler emits a `[webui] {json…}` line
for every request, including the `/health` probe that the Docker
HEALTHCHECK and ALB target group each hit a few times per second. The
upstream has no env var or config knob to silence or filter access
logs; we were working around it with a `grep -v` in `start.sh` that
matched on the JSON substring `"path": "/health", "status": 200`, which
is fragile to any change in the log format.

**Fix:** Return early from `log_request` when the HTTP status is `200`.
Drops every successful access log (health probes, normal session
traffic, static assets, streaming polls); keeps 3xx/4xx/5xx and the
non-numeric `'-'` fallback visible. Exception logs from `do_GET` /
`do_POST` are emitted via a different code path and are unaffected.

### `03-bedrock-skip-live-discovery.patch`

**Target:** `api/routes.py` (`_handle_live_models`).

**Problem:** The WebUI merges live-fetched models into the dropdown
via `GET /api/models/live`. For Bedrock, that live fetch calls AWS's
`ListFoundationModels` / `ListInferenceProfiles` and returns every
model available to the ECS task role — including Nova, Llama, old
Claude variants, DeepSeek, etc. The frontend then merges those with
our curated `providers.bedrock.models` list from `config.yaml`, so
the dropdown ends up showing ~10 entries instead of the 3 we want.

**Fix:** Return an empty list from `_handle_live_models` when
`provider == "bedrock"`. The static endpoint still serves our three
curated entries via `providers.bedrock.models`, and the frontend
merge becomes a no-op. Other providers (OpenRouter, Anthropic,
Copilot, etc.) keep their live discovery — DOH deployments on those
providers genuinely want to see account-available models.

### `04-policy-proxy-reauth-url.patch`

**Target:** `static/workspace.js` (`api` helper).

**Problem:** When DOH's `doh_session` cookie expires while the WebUI is
open, API requests receive an auth challenge from the policy proxy. The
old generic WebUI behavior redirected 401s to `/login`, which does not
exist in DOH's policy-proxy flow and strands the user after reauth.

**Fix:** Honor the policy proxy's `X-DOH-Auth-URL` response header on
401. The frontend performs a top-level navigation to that URL, avoiding
cross-origin fetch redirects that the WebUI CSP blocks.
