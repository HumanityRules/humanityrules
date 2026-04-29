# WebUI patches

DOH-owned fixes against the `ghcr.io/nesquena/hermes-webui` image tree
(`/apptoo/` inside the container). Applied at **image build time** by the
`Dockerfile`, not at container start — the WebUI lives in ephemeral image
layers (unlike the agent, which lives on EFS), so a rebuilt image always
carries fresh patches and there's no need for a boot-time `apply.py`.

Each patch targets a path relative to `/apptoo/`. The Dockerfile runs
`patch -p1 -d /apptoo -i <file>` for every `*.patch` here, then runs
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
