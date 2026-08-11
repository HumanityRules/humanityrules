# Landing-page mockups: a private decision aid

Two fast-and-rough HTML mockups of the two page designs specced in `duet/landing/spec.md`, built so you can view them side by side and pick which to ship. Open `index.html` here, or `card/index.html` and `compact/index.html` directly. Nothing here is production work and nothing is committed.

## Read this before comparing layouts

1. **The Card (`card/`) follows its specced design.** Part 3 of the spec gives it four implementation-complete screen specs; this mockup implements them: element inventories, hierarchy, the plans-grid geometry, section order.
2. **The Compact (`compact/`) is an improvisation over real content.** Its screen-level design pass was deferred, so its layout here is invented, in the same visual family as the Card, just to make its nine sections viewable. Discount any layout preference between the two pages accordingly: you are comparing the Card's specced layout against a sketch. Compare the Compact on its content and structure, not its looks.

## What is real and what is fiction

1. Copy marked FINAL in the spec appears character-for-character on both pages.
2. Every yellow `[PLACEHOLDER: ...]` highlight is fiction. On the Compact these are the founder blanks from the six gate questions (capacity N, response windows, cadences, incident and exit terms, partner pricing, program end date) filled with invented but plausible values; a yes to the gate means replacing each with your real number. On the Card they are build-time slots the spec leaves open: the access-today cells, the figure-caption capture date, the signature statement (the spec's scaffold, which never ships; the real one comes from the voice loop), the current-uses line, the anchors line, and the conditional onboarding sentence.
3. The bordered placeholder boxes stand in for assets that do not exist yet: Figure 1 (the hermesvmendi01 control-plane card), Figure 2 (the agent doing the Sheets append and read-back at the same URL), and your portrait. Each box's label says exactly what the real capture will show, per the figure specs.
