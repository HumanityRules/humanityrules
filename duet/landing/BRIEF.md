# Brief: the new Humanity Rules landing page

This folder tree is a design process run as a series of agent conversations. The final deliverable of the whole tree is a design spec for a new humanityrules.io landing page. Spec only: no implementation, no shipped HTML. Your conversation is one node in the tree; your topic tells you what this node must produce and may point you at other nodes' transcripts. Read whatever it points you to.

## The problem

The current landing page works hard at selling the product, but it doesn't inspire trust. Visitors are smart enough to detect that this is a small company, and the page leaves their real questions unanswered: who is behind this? Is it venture-backed or a solo founder? Is this a legitimate company or an anonymous operation that's going to scam me? The new page has to answer that skepticism while letting a prospective customer actually understand the product.

## What "good" means (acceptance criteria)

1. Every section earns its place by building trust, building product comprehension, or both. A section that does neither gets cut.
2. Pragmatic: only content that can actually be produced today. No invented evidence, no sections a solo founder can't staff.
3. Must never look AI-generated. This is a hard constraint on copy direction, not a later polish step.
4. Authentic: reads like a specific person built a specific thing.
5. Truthful, but not obsessively. A smart visitor must never catch a lie. But authenticity is voice and specificity, not exhaustive disclosure: do not turn the page into a claims ledger, a hedge audit, or a legal document. When in doubt, say less and stay concrete.
6. Concrete to the screen. Anything proposed must be describable as "what the visitor sees". If a reader of the spec has to ask "what does that mean?", it is not done.
7. Conclusions must come from alternatives explored and beaten, not from the first idea that sounded right. Record what was rejected and why.

## Hard constraints

- Pricing is on the page. Its form and position are open; its existence is not.
- Medium and technique are out of scope. For any dynamic or rich content, describe what the visitor sees and what it must prove, storyboard level: opening state, what changes, end state. Do not decide or debate video vs GIF vs HTML animation vs live embed; that decision comes after this process.
- Deliverables are design-spec prose. Building a quick prototype in a scratch subfolder of your conversation folder to settle a dispute is allowed; shipping code is not.

## Strong priors (Victor's; challengeable with good reasons)

- Show the product working before explaining it. The page should make the product immediately tangible.
- No "problem" section and no "solution" section. If something equivalent earns its way back in another shape, that is acceptable, but the default is gone.

## Facts you may use

- Founder: Victor Mendiluce. Solo, self-funded, no VC. Based in Millbrae, California. A California LLC is mid-formation, not filed yet, so no legal-entity name on the page for now. Everything about the founder is available for use on the page: name, face, location, background, funding status.
- Stage: pre-beta, no customers yet. Seven friends-and-family agent deployments are live.
- The product, positioning, plans and pricing are all in this repo; the repo root is a few directories up from your working folder. The current landing page: `humanityrules_app/templates/humanityrules_app/landing/` and `humanityrules_app/views/landing.py`. Product overview: `AGENTS.md` at the repo root. Docs index: `docs/AGENTS.md`. Plans and pricing: `docs/billing_design.md`.
- The current page is evidence, not a constraint. Nothing in it is locked; reuse what's good, discard the rest.
