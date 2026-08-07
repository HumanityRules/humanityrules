---
name: codex-implement
description: Implementation workflow where Codex (gpt-5.6-sol, xhigh) writes the code and tests from a high-level brief, Claude orchestrates and reviews the code (never the tests), and Sonnet 5 rewrites the prose for new readers.
disable-model-invocation: true
---

# Codex implements, Claude reviews, Sonnet writes the prose

Each model works what is strong at: Codex the machine-verified work, Sonnet 5 the prose a human reads, Fable the design conversation and high-level architecture.

## The brief

A scratchpad file stating **what and why, never how**. Implementation shape — files, names, structures, control flow — is Codex's to choose; the review absorbs that freedom, and shape problems come back as review findings, not up-front prescriptions.

- Point at the design doc for locked decisions; state inline only what was decided in conversation and isn't recorded anywhere yet.
- List what it must not touch, and that it must not commit.
- Ask it to report the design decisions it had to make, and test results.
- Never offer your compressed decision vocabulary as comment text — it gets echoed verbatim into docstrings.

```bash
codex exec --full-auto -m gpt-5.6-sol -c model_reasoning_effort=xhigh - < BRIEF.md > LOG.md 2>&1
```

Run in the background and redirect to a scratchpad log (the harness task file stays empty when stdout is redirected).

## Review

- Review the implementation only. **Never open, review, or run the tests** — Codex runs them and reports counts.
- Send findings back as suggestions — Codex decides what to fix. Anything it declines, it must justify; a declined finding is a disagreement for Victor to arbitrate, presented with both positions.

## Prose pass

After Codex is green and reviewed, a Sonnet 5 subagent gets the diff and the design doc, with this single objective:

> Rewrite the new and changed comments and docstrings so it is easy for a new reader — someone reading this code for the first time — to build a mental model of how the system works. Use plain human language.

Apply its edits directly. No verification loop — Victor's reading is the acceptance test.
