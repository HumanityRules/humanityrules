---
name: simplify-mental-model
description: Simplify code and system design by reducing the concepts, relationships, dependencies, and inferences needed to understand and predict behavior. Use when asked to simplify or refactor for clarity, improve naming or control flow, clarify state and responsibility boundaries, reduce coupling, correct dependency direction, or review whether a design is genuinely simpler while preserving behavior.
---

# Simplify Mental Model

Optimize for the smallest accurate mental model, not the fewest lines, functions, types, or characters. Accept a little more code or a longer name when it removes ambiguity, inference, or navigation.

Treat a dependency as anything one part must know about another, not only an import or package dependency.

## Build the Current Mental Model

1. Start at the entry point a new reader would use.
2. Describe the main path as a short sequence of domain actors, actions, and boundaries.
3. Identify the concepts, states, relationships, and dependencies a reader must hold simultaneously.
4. Separate essential domain complexity from complexity introduced by the implementation.

## Find Sources of Mental Load

Look for:

1. Generic names that omit the actor, role, source, destination, or result.
2. Phase names that describe an implementation ritual rather than where the operation is going.
3. Names that describe an object's history instead of its current meaning.
4. Main-path orchestration obscured by protocol mechanics or policy details.
5. Downstream decisions that reconstruct facts already known upstream.
6. Types, wrappers, helpers, or layers that introduce vocabulary without compressing explanation.
7. Components that know policy or domain facts their responsibility does not require.
8. Dependencies that point against the direction of responsibility or force unrelated changes to move together.

## Design a Smaller Model

1. Use stable domain actors consistently across names and boundaries.
2. Choose the shortest name that preserves the concept, not the shortest name possible. Prefer explicit directional names when direction matters.
3. Name values by what they mean now. Represent provenance explicitly only when later behavior depends on it.
4. Make the entry point read as the system's story. Keep policy decisions visible and move lower-level mechanics behind names that state their outcome.
5. Treat every new type as a concept the reader must retain. A boundary makes a type eligible, not justified. Add one only when it makes invalid states unrepresentable or removes more explanation than it adds.
6. Pass or return a fact when it becomes known instead of making downstream code infer it from construction details.
7. Place decisions in the layer that owns the policy. Let lower-level mechanisms expose capabilities and results without knowing the caller's policy.
8. Make each component depend only on concepts required by its responsibility. Prefer dependencies on narrower, more stable concepts.
9. Remove indirection, abstractions, states, and external dependencies that do not earn their conceptual cost.
10. Preserve behavior unless the user explicitly authorizes a behavior change.

## Compare Before and After

Evaluate the change by asking:

1. How many concepts and relationships must a reader remember?
2. How many files or definitions must they visit to understand the main path?
3. Which facts must they infer rather than read directly?
4. Can they predict what happens next from the entry point?
5. Does each dependency make conceptual sense, and does it point in the direction of responsibility?
6. Does every remaining abstraction eliminate more explanation than it introduces?
7. What does each new type let the reader forget?

Do not claim simplification solely from reduced line count, fewer functions, or shorter names.

## Review With a Fresh Reader

For a non-trivial change, give a fresh-context reviewer the relevant code or diff without giving it the intended conclusions. Ask the reviewer to:

1. Explain the system's main path in its own words.
2. Identify terms it cannot understand at their point of use.
3. Identify hidden knowledge dependencies and reversed responsibility boundaries.
4. Call out abstractions that add more concepts than they remove.

Iterate on concrete confusion until the remaining complexity is essential or outside the task's scope.

## Report the Result

Explain the resulting mental model as a short flow. Identify the concepts, inferences, and dependencies removed, plus any deliberate tradeoff such as a longer name or an additional type.
