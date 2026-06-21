---
name: hermes-update-check
description: Evaluate whether we can update the pinned Hermes Agent and Hermes WebUI versions by rebasing the DOH fork branches (doh/v* in DevOpsHeroAI/hermes-agent and hermes-webui) onto a newer upstream release, which DOH commits drop out as landed-upstream or need re-anchoring, and what upstream changes have landed since our pins. Use when the user asks about Hermes update status, whether our forks still carry needed changes, or what has improved upstream.
---

# Hermes Update Check

Updating Hermes = rebasing our fork branch onto a newer upstream release, then moving
the submodule pin. This skill records where everything lives and the project-specific
traps a bump can spring.

## How DOH's changes are carried

Both components are **vendored forks**: a private mirror with a `doh/v<upstream-version>`
branch = upstream-at-that-tag + DOH commits on top. The monorepo points at a chosen
commit via a git submodule.

- **Agent** — submodule at `template_repos/hermes_agent/vendor/hermes-agent`, pinned to
  `doh/v<ver>` in `DevOpsHeroAI/hermes-agent` (mirror of `NousResearch/hermes-agent`).
  The Dockerfile COPYs it verbatim — no base image.
- **WebUI** — submodule at `vendor/hermes-webui`, pinned to `doh/v<ver>` in
  `DevOpsHeroAI/hermes-webui` (mirror of `nesquena/hermes-webui`). The Dockerfile does
  `FROM ghcr.io/nesquena/hermes-webui:${WEBUI_BASE_VERSION}` and overlays the fork
  source onto `/apptoo` (`build/overlay-webui.sh`).

So the WebUI has **two things that must agree**: the base image tag
(`WEBUI_BASE_VERSION` ARG) and the fork branch the submodule points at. The fork
branch commits `.doh-upstream-version`, and `build/check-vendor.sh` fails the build
if it doesn't equal the base tag. A WebUI bump moves **both** in lockstep.

Rebasing happens in the workbench clones at `hermes-vendor-work/<repo>` — the only
checkouts with full history (the submodules are shallow). There, `origin` is upstream
and `doh` is the mirror.

## Bumping

1. **Pin** = the `doh/v<ver>` the submodule points at; for WebUI also the
   `WEBUI_BASE_VERSION` ARG. Latest upstream: git tags (agent), container tags (webui).
2. **Rebase** `doh/v<old>` onto the new tag in the workbench clone.
3. **Skim these areas** for behavior we depend on:
   - Agent: Bedrock path, `resolve_provider_client`, prompt caching, provider registry,
     `tools/lazy_deps.py` pins (esp. `provider.anthropic` — our Dockerfile `anthropic==`
     pin must match the lazy path, NOT the `pyproject.toml` extra, which can disagree).
   - WebUI: model list handling, access logs, Bedrock live discovery, reasoning/thinking
     events, streaming perf, request-gating middleware (see landmines).
4. **Land it:** push the rebased `doh/v<new>` branch + the new upstream tag to `doh`;
   for WebUI update `.doh-upstream-version` and `WEBUI_BASE_VERSION` together; check out
   `doh/v<new>` in the submodule and commit the gitlink via `build/vendor-commit.sh`
   (not a blind `git add -A` — it can ship an unintended pin bump).

## Behavior-change landmines (a clean rebase is not a working bump)

Rebase conflicts only surface code we touched. A bump also ships **new behavior** that
breaks DOH's topology with zero conflicts — so boot the stack and exercise the real
browser→proxy→Caddy→WebUI path before declaring it safe.

- **New request-gating middleware.** Diff for newly-added `_check_csrf` / origin / Host /
  auth gates on POST/PUT/PATCH. DOH's proxy rewrites `Host` to the loopback upstream and
  carries the real host in `X-Forwarded-Host`, so any same-origin check that compares
  `Origin` vs `Host` will 403 unless opted out. (v0.51.267 #3642 added exactly this; fix =
  `HERMES_WEBUI_TRUST_FORWARDED_HOST=1`.) Grep the new-range changelog for
  `csrf|cross-origin|forwarded-host|reverse prox` and **read the operator notes** — they
  call out env vars to set.
- **A 403/4xx on a POST = likely 501 downstream.** The policy proxy pools connections
  (httpx); a handler that returns an error without draining the request body desyncs the
  next pooled request, surfacing as `501 Unsupported method ('<json-body>POST')`. The
  mangled method line IS the prior request's undrained body — trace back to *which POST got
  the 4xx*, that's the real bug.
- **New env vars must be allowlisted twice.** The WebUI runs inside the nono sandbox. A new
  `HERMES_WEBUI_*` (or any) env var needs BOTH a Dockerfile `ENV` AND an entry in
  `humr_runtime/hermes-nono-profile.json` `allow_vars` — the sandbox silently strips
  anything not on the allowlist, so a Dockerfile-only ENV has no effect on `server.py`.
  (`/proc/<pid>/environ` reads are unreliable here; confirm with `nono run --profile … --
  env | grep VAR` or a behavioral test.)
- **Bundled skill set drifts.** `build/prune-skills.sh` allowlists bundled skills by path
  and `exit 1`s if one is missing → build failure. Upstream moves skills bundled→optional
  between releases; diff our allowlist against the new tag's `skills/*/SKILL.md` and drop
  entries that vanished.
- **Skill-name collisions → "Ambiguous skill name … Refusing to guess".** The loader
  (`tools/skills_tool.py`, since ~v2026.6.x) hard-errors when one bare name resolves in more
  than one place, where pre-bump it silently preferred the local copy. Our layout avoids
  this by keeping `skills.external_dirs: []` (image skills are *seeded* into
  `$HERMES_HOME/skills` by `sync_skills()`, not read as a second dir — see
  [[project_hermes_skills_ownership]]). One residual source survives because it reproduces
  inside the seeded copy: **flat `<name>.md` inside another skill.** Loader "Strategy 3"
  rglobs for `<name>.md` anywhere under a search dir and treats each as a skill, so
  `creative/popular-web-designs/templates/{notion,posthog}.md` collide with the
  `notion`/`posthog` skills. `prune-skills.sh` deletes those specific template files
  (tolerant — already-absent is logged, not fatal). On bump, re-derive the colliding set:
  for each `<name>.md` under any kept skill, flag it if a skill dir of that bare name also
  exists. **Do NOT re-point `external_dirs` at the image tree** — that reintroduces the
  full 38-way collision.
- **A skill silently missing from the catalog.** `sync_skills()` copies image skills as the
  unprivileged agent user; a source file that isn't world-readable (stray 0600) makes
  `copytree` fail and the skill vanishes with only a `! Failed to copy` log line. The
  Dockerfile normalizes modes after `COPY skills/`, but if you add DOH skills or a bundled
  file ships odd perms, confirm `find skills -type f ! -perm -044` is empty.

## Report

Under ~50 lines, two sections (Agent, WebUI): pin + how far behind; per-commit verdict
(landed / clean / needs re-anchor) with an upstream `file:line`; 2–4 background bullets
including any landmines hit; bump-or-hold recommendation. Agent and WebUI are
version-coupled (e.g. WebUI #3443's `openai-api` picker fix only works because the agent
registry uses that slug) — call out fixes that need both. The user has prior-run context;
give the delta.
