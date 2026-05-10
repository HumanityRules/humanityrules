---
name: hermes-update-check
description: Evaluate whether we can update the pinned Hermes Agent and Hermes WebUI versions, drop any DOH-owned patches under template_repos/hermes_agent/patches/ or patches-webui/, and what upstream changes have landed since our pins. Use when the user asks about Hermes update status, whether patches are still needed, or what has improved upstream.
---

# Hermes Update Check

Two components, same questions for each:

1. Is there a newer version? How far is upstream ahead of our pin?
2. For each DOH-owned patch, has the fix landed upstream?
3. What else changed that matters to us?

## The two pins

Both live in `template_repos/hermes_agent/Dockerfile`:

- **Hermes Agent** — `git clone --branch <tag>` from `NousResearch/hermes-agent`. Patches in `patches/` (plus `patches/overlay/` for DOH-owned files upstream doesn't have).
- **Hermes WebUI** — `FROM ghcr.io/nesquena/hermes-webui:<tag>` (source: `nesquena/hermes-webui` on GitHub). Patches in `patches-webui/`, applied against `/apptoo/` in the image.

Each patch has a README explaining what it fixes — read it before deciding the verdict.

## Workflow per component

1. **Read the pin** from the Dockerfile.
2. **List available versions** upstream (git tags for the agent, container tags for the webui) and see how far `main` / latest is ahead.
3. **For each patch**: read it, identify the target file and the key identifier being changed, fetch the current upstream file, and classify:
   - **Landed** — upstream already has the fix (verbatim or via refactor that covers our case). Confirm semantics before declaring obsolete.
   - **Not landed** — our anchor still matches. Keep the patch; check whether it still applies cleanly.
4. **Overlay files** — check whether upstream now ships the file; if so, confirm it does what ours does before removing.
5. **Background changes worth flagging** — skim the areas we care about:
   - Agent: Bedrock path, `resolve_provider_client`, prompt caching, provider registry.
   - WebUI: model list handling, access logs, Bedrock live discovery, reasoning/thinking events, streaming perf.

## Reporting format

Keep the whole report under ~50 lines. Two sections, Agent and WebUI, each with:

- **Pin** — current version, how far behind latest.
- **Per-patch verdict** — one bullet per patch: landed / not landed, with a one-line justification pointing at upstream file:line.
- **Overlay verdict** (agent only).
- **Background changes** — 2–4 bullets on areas above.
- **Recommendation** — bump or hold, and which patches to delete or re-anchor if bumping.

The user has context from prior runs; they want the delta.

## Gotchas

- Patch line numbers drift. Grep for the identifier, not the line in the patch header.
- "Landed" ≠ identical code. A refactor may cover our case differently — verify semantics.
- Dead patches (applying cleanly as a no-op because the fix landed) mask drift. Prefer removing.
- Agent patches apply with `patch -p1 -N` (idempotent); WebUI patches go through `apply-patches.py` at image build and a fuzz is a build failure.
