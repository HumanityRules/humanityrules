---
name: hermes-update-check
description: Evaluate whether we can update the pinned Hermes Agent version, drop any of the DOH-owned patches under template_repos/hermes_docker_agent/patches/, and what upstream changes have landed since our pin. Use when the user asks about the Hermes update status, whether patches are still needed, or what has improved upstream.
---

# Hermes Update Check

Decide three things and report them back:

1. **Should we bump the pin?** — is there a newer tag, how far is `main` ahead?
2. **Which patches can we drop?** — for each patch under `template_repos/hermes_docker_agent/patches/`, has the fix landed upstream?
3. **What else changed upstream that matters to us?** — Bedrock path, auxiliary clients, prompt caching, config/model handling.

The upstream repo is **`NousResearch/hermes-agent`**. The pinned version lives in `template_repos/hermes_docker_agent/Dockerfile` (the `--branch` arg of `git clone`).

## Workflow

### 1. Read the pin

```bash
grep -E "branch v[0-9]" /Users/vmendi/websites/devopshero/template_repos/hermes_docker_agent/Dockerfile
```

Record the tag (e.g. `v2026.4.16`).

### 2. Compare pin vs. main and list tags

```bash
gh api 'repos/NousResearch/hermes-agent/tags?per_page=30' --jq '.[].name'
gh api repos/NousResearch/hermes-agent/compare/<PIN>...main \
  --jq '{ahead: .ahead_by, behind: .behind_by, total: .total_commits}'
gh api repos/NousResearch/hermes-agent/commits/main \
  --jq '{sha: .sha, date: .commit.author.date, message: .commit.message}'
```

- If the top tag **equals** our pin, note that there is **no newer tagged release** — moving to `main` means unpinning.
- If there is a newer tag, that's the upgrade candidate.

### 3. Fetch the files each patch touches

**Use `gh api` with base64 decoding, not `curl` to raw.githubusercontent.com** — the raw host often fails with curl exit 35 from this machine, while `gh api contents` works reliably.

```bash
gh api 'repos/NousResearch/hermes-agent/contents/run_agent.py?ref=main' \
  --jq '.content' | base64 -d > /tmp/hermes_run_agent_main.py
gh api 'repos/NousResearch/hermes-agent/contents/agent/auxiliary_client.py?ref=main' \
  --jq '.content' | base64 -d > /tmp/hermes_aux_client_main.py
```

Fetch any other file a patch targets (check `--- a/<path>` header in each `.patch` file).

### 4. Per-patch verdict

For each `NN-*.patch` in numeric order:

- Read the patch (`Read template_repos/hermes_docker_agent/patches/NN-...patch`) and extract:
  - the target file (from `--- a/<path>`)
  - the key identifiers being changed (e.g. `_anthropic_preserve_dots`, `is_native_anthropic`, `auth_type == "aws_sdk"`)
- `grep` those identifiers in the upstream file.
- Classify:
  - **Landed (verbatim)** — the replacement string appears unchanged → drop the patch.
  - **Landed (generalized)** — the old anchor is gone; a different refactor covers the same case (e.g. one general helper replaces three scattered edits) → drop the patch, but verify the new code path covers our use case.
  - **Not landed** — the old anchor still matches and our fix is absent → keep the patch, and check if line offsets drifted (the patch may still apply with `-N`, or may need re-anchoring).
- If a DOH-owned PR is associated with the patch (e.g. patch README / commit message mentions a PR number), check its state:
  ```bash
  gh api repos/NousResearch/hermes-agent/pulls/<N> \
    --jq '{title, state, merged, updated_at}'
  ```

### 5. Overlay files

Files under `patches/overlay/` are DOH-owned files that don't exist upstream. For each:

```bash
gh api "repos/NousResearch/hermes-agent/contents/<path>?ref=main" --jq '.name' 2>&1
```

If upstream now has the file, the overlay may be redundant — inspect and confirm the upstream version does what ours does before removing.

### 6. Background improvements since the pin

Even when patches are unaffected, users want to know what improved. Diff areas we care about:

- **Bedrock path** — `grep -n "bedrock\|bedrock_converse\|AnthropicBedrock" /tmp/hermes_run_agent_main.py` vs pin. Call out new modes (e.g. `bedrock_converse` vs `anthropic_messages` for Bedrock + Claude), guardrail config, region handling.
- **Auxiliary client resolution** — `resolve_provider_client` in `agent/auxiliary_client.py`. Note new `auth_type` branches, provider aliases, any Bedrock support.
- **Prompt caching** — look for `_use_prompt_caching`, `_anthropic_prompt_cache_policy`, `apply_anthropic_cache_control`. A scattered set of conditions collapsing into a single helper is worth flagging.
- **Model / provider registry** — `hermes_cli/auth.py` `PROVIDER_REGISTRY`. New providers or aliases may let us retire workarounds.
- **New tags / release cadence** — the list from step 2.

To compare pinned vs main for a single file:

```bash
gh api "repos/NousResearch/hermes-agent/contents/run_agent.py?ref=<PIN>" \
  --jq '.content' | base64 -d > /tmp/hermes_run_agent_pin.py
diff <(grep -n "bedrock" /tmp/hermes_run_agent_pin.py) \
     <(grep -n "bedrock" /tmp/hermes_run_agent_main.py) | head -60
```

Keep these diffs small and targeted — `run_agent.py` is ~12k lines; full diffs are useless.

## Reporting format

Report back to the user in this structure, tight bullets only:

- **Pin** — `<tag>`, published `<date if easy>`. `main` is `<N>` commits ahead. Latest tag: `<tag or "same as pin">`.
- **Per-patch verdict** — one bullet per patch:
  - `NN-xxx.patch` → **landed / landed-generalized / not landed**, one-line justification with file:line reference in upstream.
- **Overlay verdict** — one bullet per overlay file.
- **Background changes worth knowing** — 3-6 bullets on the Bedrock / aux-client / caching / registry areas, each tied to a concrete upstream location.
- **Recommendation** — bump or hold, with a one-sentence reason. If bumping, list exactly which patches to delete and which to re-anchor.

Keep the whole report under ~40 lines. The user already has context from prior runs; they want the delta.

## Gotchas

- `curl https://raw.githubusercontent.com/…` may fail with exit 35 on this machine. Default to `gh api … | base64 -d`.
- `gh api` with unquoted `?ref=…` queries trips zsh glob matching — always single-quote the path.
- Patch anchors include line numbers; don't trust them. Always grep for the *identifier* being changed, not the line number in the patch header.
- "Landed" ≠ "same code." Upstream may refactor to a different shape (helper function, different condition). Read the upstream code and confirm the semantics cover our case before declaring the patch obsolete.
- Our patches apply with `patch -p1 -N` (idempotent). A patch that's "landed verbatim" becomes a no-op and is safe to leave, but carrying dead patches masks drift — prefer removing.
