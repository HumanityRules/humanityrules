---
name: hermes-update-check
description: Evaluate whether we can update the pinned Hermes Agent and Hermes WebUI versions, drop or re-anchor any DOH-owned patches under template_repos/hermes_agent/build/patches-agent/ or build/patches-webui/, and what upstream changes have landed since our pins. Use when the user asks about Hermes update status, whether patches are still needed, or what has improved upstream.
---

# Hermes Update Check

Two components, same questions for each:

1. Is there a newer version? How far is upstream ahead of our pin?
2. For each DOH-owned patch, has the fix landed upstream?
3. What else changed that matters to us?

## The two pins

Both live in `template_repos/hermes_agent/Dockerfile`:

- **Hermes Agent** — `git clone --branch <tag>` from `NousResearch/hermes-agent` (`ARG HERMES_AGENT_REF`). Patches in `build/patches-agent/`.
- **Hermes WebUI** — `FROM ghcr.io/nesquena/hermes-webui:<tag>` (source: `nesquena/hermes-webui` on GitHub). Patches in `build/patches-webui/`, applied against `/apptoo/` in the image.

`build/patches-webui/README.md` documents each WebUI patch; agent patches carry their rationale in the patch header. Read it before deciding the verdict.

**Both patch sets apply via `build/apply-patches.py`, which runs `patch -p1 -F 0` (zero fuzz) — a changed context line is a hard build failure for either set.** Line-number offset is tolerated; changed context is not.

## Workflow per component

1. **Read the pin** from the Dockerfile.
2. **List available versions** upstream (git tags for the agent, container tags for the webui) and see how far `main` / latest is ahead.
3. **For each patch**: read it, identify the target file and the key identifier being changed, fetch the current upstream file, and classify:
   - **Landed** — upstream already has the fix (verbatim or via refactor that covers our case). Confirm semantics before declaring obsolete.
   - **Not landed** — our anchor still matches. Keep the patch; check whether it still applies cleanly.
4. **Background changes worth flagging** — skim the areas we care about:
   - Agent: Bedrock path, `resolve_provider_client`, prompt caching, provider registry, `tools/lazy_deps.py` pins (esp. `provider.anthropic` — our Dockerfile `anthropic==` pin must match the lazy path, NOT the `pyproject.toml` extra, which can disagree).
   - WebUI: model list handling, access logs, Bedrock live discovery, reasoning/thinking events, streaming perf, **and request-gating middleware (CSRF / origin / auth) — see "Behavior-change landmines" below.**

## Behavior-change landmines (a bump can break runtime even when every patch applies clean)

The patch-drift pass only catches code we edited. A WebUI/Agent bump also ships **new behavior** that can break DOH's topology with zero patch conflicts. Before declaring a bump safe, **boot the stack and exercise the real browser→proxy→Caddy→WebUI path**, not just the upstream-tree dry-run. Specifically check:

- **New request-gating middleware.** Diff for newly-added `_check_csrf` / origin / Host / auth gates on POST/PUT/PATCH. DOH's proxy rewrites `Host` to the loopback upstream and carries the real host in `X-Forwarded-Host`, so any same-origin check that compares `Origin` vs `Host` will 403 unless opted out. (v0.51.267 #3642 added exactly this; fix = `HERMES_WEBUI_TRUST_FORWARDED_HOST=1`.) Grep the new-range changelog for `csrf|cross-origin|forwarded-host|reverse prox` and **read the operator notes** — they call out env vars to set.
- **A 403/4xx on a POST = likely 501 downstream.** The policy proxy pools connections (httpx); a handler that returns an error without draining the request body desyncs the next pooled request, surfacing as `501 Unsupported method ('<json-body>POST')`. The mangled method line IS the prior request's undrained body — trace back to *which POST got the 4xx*, that's the real bug.
- **New env vars must be allowlisted twice.** The WebUI runs inside the nono sandbox. A new `HERMES_WEBUI_*` (or any) env var needs BOTH a Dockerfile `ENV` AND an entry in `doh_runtime/hermes-nono-profile.json` `allow_vars` — the sandbox silently strips anything not on the allowlist, so a Dockerfile-only ENV has no effect on `server.py`. (`/proc/<pid>/environ` reads are unreliable here; confirm with `nono run --profile … -- env | grep VAR` or a behavioral test.)
- **Bundled skill set drifts.** `build/prune-skills.sh` allowlists bundled skills by path and `exit 1`s if one is missing → build failure. Upstream moves skills bundled→optional between releases; diff our allowlist against the new tag's `skills/*/SKILL.md` and drop entries that vanished.

## Verifying re-anchored patches

When a hunk fails on the bumped tree, **regenerate it with `diff -u` against a real `git clone --branch <tag>`**, not a hand-edited header — manual `@@` line-count math is the #1 source of silent re-anchor failures. Note the GitHub `contents` API can return a copy that differs by a few lines from `git clone --branch`; the build uses the clone, so verify against the clone.

## Reporting format

Keep the whole report under ~50 lines. Two sections, Agent and WebUI, each with:

- **Pin** — current version, how far behind latest.
- **Per-patch verdict** — one bullet per patch: landed / not landed, with a one-line justification pointing at upstream file:line.
- **Background changes** — 2–4 bullets on areas above, including any behavior-change landmines hit.
- **Recommendation** — bump or hold, and which patches to delete or re-anchor if bumping. Note the agent and WebUI are version-coupled (e.g. WebUI #3443's `openai-api` picker fix only works because the agent registry uses that slug), so call out when a fix needs both.

The user has context from prior runs; they want the delta.

## Gotchas

- Patch line numbers drift. Grep for the identifier, not the line in the patch header.
- "Landed" ≠ identical code. A refactor may cover our case differently — verify semantics.
- Dead patches (the fix landed upstream) apply as a no-op and mask drift. Remove them (e.g. agent `07-package-hermes-cli-subpackages` landed and was deleted at the v2026.6.5 bump).
- Both patch sets apply via `apply-patches.py` (`patch -p1 -F 0`) at image build — any context-line drift is a build failure, not a silent fuzz. Always dry-run against a fresh `git clone --branch <tag>` before building.
- "Patches all apply" ≠ "the bump works." Always boot the stack and test the real browser/proxy path — see Behavior-change landmines.
