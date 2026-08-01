---
name: simplify-mental-model
description: Simplify code or system design through a staged, user-gated process.
disable-model-invocation: true
---

# Simplify Mental Model

## What "Simpler" Means

Complexity is what it costs a reader to build an accurate mental model. It has four dimensions:

1. Retention: how many concepts and relationships they must hold simultaneously.
2. Navigation: how many files or definitions they must visit to follow an important code path.
3. Inference: how many facts they must derive rather than read directly.
4. Prediction: how well the entry point lets them predict what happens next.

A change is a simplification when it lowers these costs while keeping the model accurate. 

Code volume is not a dimension: more code or a longer name can still be a simplification.

Treat a dependency as anything one part must know about another, not only an import.

## Symptoms of Mental Load

1. Names that are not enough, at the point of use, to know what the thing means or does now.
2. An important code path that does not read as a sequence of steps, because each step's detail is inlined instead of named.
3. Downstream decisions that reconstruct facts already known upstream.
4. Abstractions that introduce vocabulary without compressing explanation.
5. Components that know facts their responsibility does not require.
6. Dependencies that point against the direction of responsibility or force unrelated changes to move together.

## Remedies

1. Use stable domain actors consistently across names and boundaries.
2. Choose the shortest name that preserves the concept, not the shortest name possible. Prefer explicit directional names when direction matters.
3. Name values by what they mean now. Represent provenance explicitly only when later behavior depends on it.
4. Make the entry point read as the system's story. Keep policy decisions visible and move lower-level mechanics behind names that state their outcome.
5. Treat every new type as a concept the reader must retain. A boundary makes a type eligible, not justified. Add one only when it makes invalid states unrepresentable or removes more explanation than it adds.
6. Pass or return a fact when it becomes known instead of making downstream code infer it from construction details.
7. Place decisions in the layer that owns the policy. Let lower-level mechanisms expose capabilities and results without knowing the caller's policy.
8. Make each component depend only on concepts required by its responsibility. Prefer dependencies on narrower, more stable concepts.
9. Remove indirection, state, and dependencies that do not earn their conceptual cost.

## Protocol

### Stage 1 — Brainstorm (turn-based, with the user)

Each step ends by presenting to the user and waiting for their reply before moving on.

1. Build the current mental model: start at the entry point a new reader would use, describe the important code paths as domain actors, actions, and boundaries, and separate essential domain complexity from complexity introduced by the implementation. Present it; the user corrects or says continue.
2. Find symptoms of mental load. Present them; the user confirms, adds, or strikes items.
3. Design the smaller model using the remedies. Present it; the user iterates.
4. Iterate until the user explicitly agrees to code changes.

### Stage 2 — Implement (subagent)

1. Hand the agreed target model plus this skill to an implementation subagent, which builds and runs the tests itself. Preserve behavior unless the user authorized a change.
2. When the subagent reports back, relay a summary of the result, including any deviation from the agreed model. Do not review, build, or test yourself.

### Stage 3 — Review (user first, user drives)

1. The user reviews first.
2. When asked, review the result against the agreed model and the cost dimensions, and report findings.
3. The user decides which findings to act on and drives the changes.
