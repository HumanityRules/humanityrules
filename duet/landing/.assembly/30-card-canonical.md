# Concept 4 developed: The Plain Product Card

**The bet, unchanged.** Skeptics distrust salesmanship. The page refuses spectacle, not information: it reads like a well-made product datasheet, ordinary, exact, and complete enough that nothing seems missing. The transaction is the trust object; Victor is the signature on it. Restraint reads as confidence only if the proof is legible, the facts are plentiful, the commercial terms are complete, and provenance is ordinary and present. Sparse is a failure mode; precise is the target.

## The page, in order

**1. Identification and proof.**
*Job:* establish in the first screen what HumR is and that it exists as a working, inhabited product, before any explanation.
*The visitor sees:* one sentence identifying the product, then one restrained access action, then two captioned figures in sequence. The sentence is the most load-bearing copy on the page: it must tell a cold visitor what kind of object Figure 1 is (a company deploys always-on AI agents; each is a running service with its own runtime, credentials, and controls; exact wording at screen-spec time). Figure 1, the deployed object: the control-plane record of a real agent, its name, service URL, running status, creation age, last deployment, and deployment history with the owner's address. Caption states only what the pixels show: "This deployment has been running since [date]." Figure 2, the object operating: the agent surface at that same service URL, the URL visible, performing one real, ordinary action that crosses a named brokered integration and ends with the changed content or substantive result legible in the frame, not the agent's claim that something happened elsewhere. Prior thread history is visible at the edge of the frame. Caption: "At that deployment's URL, the agent [did X]." The repeated service URL is the join between the figures; no caption asserts a backend relationship the screens do not expose. Storyboard: opening state is the request, the change is the integration action, the end state is the result content. Nameplate first, then the machine running: the datasheet's native order.

**2. Product facts.**
*Job:* complete comprehension in datasheet register; every fact a technical evaluator needs, zero adjectives of reassurance.
*The visitor sees:* a compact spec account: an always-available agent on a hosted runtime with persistent state; credential handling stated at exactly the level the current interface supports (a credential is provisioned once by an admin, its secret stays hidden, and it is shared to everyone, a workspace, or a named user), with the credential screen as a supporting figure; the deployment boundary (Trial and Operator run in HumR's cloud, Team and Enterprise in the customer's AWS account); one line of provenance, "built on the open-source Hermes harness"; and a "Current uses include…" line listing only task categories verifiable from the seven live deployments, omitted entirely if producing it would require generalizing beyond what they actually do. The credential row reading "User · victor@…" and the deployment history reading the same address is a rhyme the attentive visitor can find; the page leaves it unremarked.

**3. Plans and commercial boundaries.**
*Job:* the page's center of gravity; the transaction presented so exactly that the pricing table itself is the trust argument.
*The visitor sees:* all four plans together: Trial free, Operator $39/month, Team $29 per agent/month, Enterprise custom. Each plan shows, with equal prominence to price: where it runs (HumR-hosted versus customer AWS), model terms (included credits versus bring-your-own), what is included, and its access reality today: purchasable now, manually provisioned, request access, or sales-led. The transaction must be truthful at the moment it is presented, not corrected later. No "most popular" ribbon, no anchoring theater, no per-plan marketing adjectives.

**4. The signature.**
*Job:* accountability for the transaction. Contract logic: terms, then signature.
*The visitor sees:* a compact block: Victor's name, face, Millbrae, California, solo, self-funded, and a short first-person signature statement of a few sentences. No origin story, no mission, no timeline.

**5. Availability and next step.**
*Job:* state the overall stage and the single truthful next action. It summarizes the condition the plan table already disclosed; it repairs nothing.
*The visitor sees:* "pre-beta" stated as such; seven friends-and-family deployments named as exactly that, not dressed as adoption; a literal description of what happens if they proceed today; one next action, verified at build time.

**6. Footer.**
*Job:* close without cosplay.
*The visitor sees:* contact, the Hermes attribution link, privacy and terms only if the documents exist, no legal-entity name until the LLC files, no empty menu architecture.

## Risk treatment: why plainness will not read as unfinished or vaporware

1. **The product works before it is explained, at this concept's own unit: an action, not a task.** One continuous state change in the real product, no elapsed-time jump, no task arc, one viewport of attention. Task arcs belong to One Real Run; an action is the largest proof this concept can carry without importing narrative machinery.
2. **Lived-in coherence.** Several mundane traces agree: the deployment is not newly created; it has been deployed more recently than it was created; the control-plane URL matches the operating surface; the agent contains prior threads; the action leaves a real result. No single trace proves authenticity, and the page claims none does. Their coherence makes the product read used rather than assembled for the page.
3. **Precision is the anti-vaporware register.** Vapor is vague. This page is exact everywhere: plan boundaries, hosting boundaries, model terms, and per-plan access reality, including which plans cannot be bought yet.
4. **Dense, not austere.** The visual register is a well-made datasheet: information-dense, well-set, ordinary. Emptiness says nothing to say; density says the product exists. Conspicuous minimalism is rejected as its own performance.
5. **The page never argues its own honesty.** No "unedited," no "no marketing fluff," no anti-staging claims. Every sentence whose job is to make the visitor believe the page is a sentence of posturing.

## The restraint discipline: refusal pairs

Rule: a refusal registers as a choice because something unexpectedly concrete occupies the slot where the genre artifact would sit. The substitute must be information, never a comment on the refusal.

1. Hero slogan → the one-sentence literal identification.
2. Simulated UI, abstract agent art, decorative dashboards → two captioned figures of the real product with real history.
3. Logo strips, testimonials, counters → the stage disclosure: seven deployments, friends and family, named as such.
4. Problem/solution theater, manifesto, persona stories, scare copy → the product-facts section and the proof itself.
5. Repeated CTAs and restated claims → one access action up top, one next action at the end, each fact stated once.
6. Empty enterprise navigation → a footer containing only real destinations.
7. Feature-card grids with equal visual weight → spec prose ordered by importance.
8. "Secure," "enterprise-grade," "fully governed" → named mechanisms: credential provisioned once and hidden, shared to a named user or workspace; Team and Enterprise run in the customer's own AWS account.

## Required assets

1. The identification sentence (hardest copy on the page; drafted and tested at screen-spec time).
2. Figure 1 capture: a real deployment with months of age, a recent deployment, running status, visible service URL. In practice Victor's own agent; this is a screenshot source, not a dogfooding argument, and the page never says "we run on it."
3. Figure 2 capture: one action meeting all four criteria: real and ordinary, completes without a time jump, crosses a named brokered integration, ends with the changed content legible in the product surface.
4. A privacy pass on both captures: publishable without heavy redaction. A frame covered in blurs reads as staged and destroys the lived-in coherence; prefer a naturally public-safe region of real history.
5. The current-uses list, verified against the seven deployments; omit the line if verification fails.
6. Per-plan access reality and the final next action, both verified at build time (Stripe live mode waits on the LLC; nothing labeled "coming soon").
7. Credential-screen capture; exact plan and limit copy from the billing design; founder block (face, facts, short signature).

## Rejected along the way (so they are not reinvented)

1. Static screenshots as sufficient proof: they show interfaces exist, not the product working.
2. Simultaneous two-pane proof: shrinks both surfaces, reads as a marketing composite.
3. Caption-as-join-key ("the credential it used, scoped to X"): the current UI exposes no per-agent credential attachment, grant dates, or resource scopes; the caption would assert what the pixels cannot show.
4. A resolved approval event in the proof: imports a second actor and narrative time, One Real Run's machinery.
5. A pending approval as the end state: the first observed behavior becomes a stoppage and reads as failure.
6. Thread-first ordering: the first product impression becomes commodity chat, "hosted Hermes."
7. A separate employee/company section: persona copy in embryo; the plan table carries that split structurally.
8. Forensic-authenticity language anywhere on the page.
9. Visual austerity as a style: performance in a different costume.

## The strongest reason not to choose this concept

The Card structurally cannot show the product's most valuable property. HumR's differentiator is persistence: an agent that keeps working after the laptop closes, across hours and days. This page's proof unit is a sub-minute action, because anything longer requires narrative time, and refusing narrative time is the concept's identity. So the one thing that most separates HumR from a chat window is asserted in the facts section rather than demonstrated, and the page asks a pricing table and typographic restraint to do the work that observed behavior does in One Real Run. That trade is rational only for a visitor who reads precision as competence and distrusts demonstration: the technical, salesmanship-allergic evaluator. For everyone else, including technical buyers who skim under time pressure, the page is trustworthy but inert: nothing on it manufactures desire, by design, and a visitor who arrives unconvinced that they want an always-on agent leaves the same way, respecting the company and buying nothing. At a stage where the company has no reputation to lend the page gravity, choosing the Card means betting that the early market is made almost entirely of the first kind of visitor, and accepting the lowest ceiling in the catalog with every other kind.
