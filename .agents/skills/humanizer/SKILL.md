---
name: humanizer
description: Write or rewrite user-facing copy so it reads human-written, then prove it with an adversarial evaluator loop. Use whenever drafting or editing marketing/landing copy, website text, or any public-facing prose in this project, and whenever the user says text "sounds like AI", asks to "humanize" or "de-slop" it, or wants copy checked for AI tells — even if they don't name this skill.
---

# Humanizer

Readers who see a lot of vendor and AI copy no longer flag it by vocabulary; they flag it by rhythm. The reliable fingerprint is metronomic sameness, and sentences optimized to *sound balanced and complete* instead of stating one specific, falsifiable fact. So the goal is never "swap the bad words": it's to make every sentence carry a mechanism, noun, or failure mode a skeptical reader could check, and to make the set of sentences uneven the way written-by-a-person text is.

Best external reference if deeper context is needed: https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing

## The tells (ranked for short copy)

1. "It's not X, it's Y" / "not just X" antithesis — the most-flagged pattern. At most one contrast construction per page; say the Y directly.
2. Dash-appositive + triad ("sandboxed, isolated, and always-on"). Typically one item is real, one redundant, one vibe.
3. Virtue-closer — ending on an abstract benefit restatement ("built for compliance"). The single biggest lever: end on a concrete, checkable fact instead.
4. Uniform cadence — every unit the same grammatical shape and length. Vary sentence count, length, and mood across units; let one break the grid.
5. List-stuffing as fake completeness ("Slack, GitHub, Notion and any MCP server"). Pick the illustrative one or two.
6. Vague absolutes doing marketing work (every/never/always) — unless the absolute is a checkable architecture fact, which is exempt.
7. SaaS lexicon: seamless, robust, fine-grained, curated, orchestrate, empower, unlock, elevate, out of the box.
8. Moody 2025-era abstractions: quietly, actually, genuinely, load-bearing, "the work", compound, signal, lands.
9. Copula avoidance ("serves as", "boasts") and -ing significance trailers ("...highlighting its commitment to").
10. Scheduled mic-drop fragments and "so you can focus on what matters" benefit tails.
11. Passive voice with the agent deleted ("entries are reviewed") — a process claim with no accountable subject is unfalsifiable. Name who.
12. Synonym-cycling the same concept across units ("a human" / "a person"). Repeating the right word reads human; elegant variation reads generated.

## House copy rules

- Discuss copy in chat before touching HTML. Iterate one element at a time; present numbered options with a recommendation.
- No em dashes.
- No swagger idioms or theatrics ("they stop cold"). The concrete fact carries the punch; state it plainly.
- Within an element (title + body), a word-root appears once.
- Across elements, deliberate reprise is fine and often good: a callback, an escalation, or the page's spine theme returning. Reprise in the SAME words (same-word repetition reads human). What reads generated is the same idea re-explained in different words as if it were new, or an element that exists only to restate another.
- Concrete beats category jargon; at most one category-label term per element.

## Workflow

### 1. Collect the true facts

Before drafting, gather the mechanisms this copy can honestly claim: from the codebase, docs/, and AGENTS.md. Ending on falsifiable facts only works if the facts are real — a hallucinated capability ("SIEM-exportable logs") once survived several editing rounds before the user caught it. Never invent a specific to sound concrete; that is the same failure as "seamless", just harder to spot. If a claim is plausible but unverified (who reviews? what's the retention?), draft it and flag it explicitly for the user to confirm.

### 2. Draft

Apply the tell list and house rules. Check cadence at the set level, not just per unit: no two adjacent units sharing a skeleton, sentence counts and lengths uneven across the set.

### 3. Adversarial eval loop

Each round, spawn a FRESH Sonnet evaluator agent (Agent tool, `model: sonnet`) with the prompt template below. Never reuse the previous round's evaluator, and never tell the judge what changed, why, or that it is looking at a revision at all: it gets the rubric and the current copy, nothing else. A judge that knows "the author applied my suggested fix" has two pressures toward approving — consistency with its own advice and cooperativeness with a collaborator — and its scores stop measuring the copy. Blind, fresh judges keep every round an independent measurement.

- Each round's judge re-scores every unit except those the user hand-locked. Revise whatever that judge puts below 5. A new judge may flag something a previous judge blessed; that is signal, not churn.
- Done: a single round comes back with every open unit and the set at 5/5 from the same judge. Give up after 5 rounds; all units and the set at 4/5 or better is the minimum acceptable landing.
- Resist late-round nitpicks that would trade texture for polish (colloquial phrases, loosely placed words, odd-but-human titles). Overpolishing is the failure mode the whole skill exists to prevent; if the only path to a 5 is sanding those off, stop and report the 4 instead.

The evaluator reliably catches what the drafter misses: root repeats between title and body, twin structures in adjacent units, deleted-agent passives, decorative specificity ("months later" with no retention spec behind it).

### 4. Deliver

Report final copy with the score trajectory, and separately list any claims flagged for factual confirmation. Apply to files only per the process rule: after the user locks the copy, or when they delegated the rewrite outright.

## Evaluator prompt template

> You are a copy evaluator. Your ONLY job: score how HUMAN-written this copy sounds. You are adversarial: assume a skeptical reader who reads vendor copy all day and flags AI-generated text on rhythm alone.
>
> [Paste "The tells" list and "House copy rules" from this skill, plus the core finding: words are weak evidence, metronomic sameness and unfalsifiable balance are the fingerprint. What reads human: concrete mechanisms, falsifiable claims, uneven lengths, repeated right words, ending on the last real fact, passing the read-aloud test.]
>
> [Paste ALL units, marking any as LOCKED: include them when judging set-level rhythm and redundancy, but score only the open ones.]
>
> For each open unit: **Score: N/5** (5 = a sharp human copywriter wrote it; 4 = human with a minor tic; 3 = ambiguous; 2 = probably AI; 1 = obviously AI), the tells present (named and quoted), and if below 5 the smallest change that would raise the score. Then a **Set score: N/5** for rhythm and redundancy across all units, including issues involving locked units (report-only).
>
> Be harsh. A 5 must be earned.
