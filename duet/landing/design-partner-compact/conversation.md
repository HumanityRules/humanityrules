# design-partner-compact

Claude: fable (xhigh) · Codex: gpt-5.6-sol (xhigh) · first: Codex

## Codex — turn 1 (2026-08-10 20:30)

I think the Compact survives, but only if the page exposes its failure conditions before it asks for an application. Otherwise it is a selective-sales CTA wearing contractual language.

My proposed structure starts like this:

1. Real deployment plus one ordinary integration action with a visible result. This establishes that there is something real to partner around, but stays deliberately compact; it cannot become the page’s climax.

2. All four commercial paths, immediately and exactly. Trial is free; Operator is $39/month; both are hosted and plainly available. Team is $29/agent/month, customer-cloud, and currently enters through the design-partner program. Enterprise remains custom and sales-led. This section prevents Team selectivity from contaminating the hosted paths with fake scarcity.

3. “Team is for you now if…” followed by “Please don’t apply yet if…”. The second list must contain revenue-costing exclusions, not harmless disqualifiers such as “you want magic.” Plausible examples include lacking a named infrastructure owner, being unwilling to grant the access the shipped deployment requires, needing production SLAs or least-privilege AWS access today, or lacking one concrete workflow the partner will actually exercise. Victor must affirm which exclusions he will enforce; we cannot choose them for him.

4. “Before you apply.” Four concise disclosures: the current AdministratorAccess requirement; vendor custody of OAuth refresh tokens and pasted provider keys where applicable; the runtime state, conversation history, logs, and infrastructure that remain in the customer account; and the control-plane, brokerage, deployment, and runtime functions that still depend on HumR. No tracing exercise, custody map, or disappearance scenario. This is informed consent, not the Walkthrough’s architecture audit.

5. “The compact.” This should be the trust climax: two short, parallel sets of statements headed “You can hold me to” and “I will hold you to,” followed by exact capacity, economics, support boundaries, incident handling, exit work, and what happens at program end. If those statements cannot be written in contractual plain language, the concept is dead.

6. “When this program ends.” A maturity trigger and successor posture stated before the founder block. Hosted checkout is irrelevant to the trigger because it can coexist now; the trigger must concern customer-cloud maturity. A mere date for reconsideration is too weak—it permits perpetual renewal of scarcity. The page needs an observable condition under which Team stops being selectively founder-onboarded, plus what replaces it.

7. Victor as the named signer: face, location, solo/self-funded status, pre-beta, and seven friends-and-family deployments. He appears after the terms because he is the counterparty who signs them, not the reason to believe the product exists.

8. The application. Its questions repeat the published acceptance and refusal criteria exactly, explain that Victor personally reviews it, give a response window, and state decline/redirect outcomes. No surprise qualification criteria after submission.

I would resist putting pricing late. Exact public pricing is not a closing detail here; showing the available hosted alternatives before the selective offer is part of what makes refusal costly rather than theatrical.

I also think the six-part gate should become six yes/no founder questions even though the first question’s factual plan scope is already settled. Repo facts establish that Trial and Operator are hosted and Team is customer-cloud; they cannot establish Victor’s willingness to publish and preserve that distinction. Each question should quote the exact statement that would appear on the page. A “yes” means “I authorize this statement and will operate accordingly,” not merely “this sounds directionally right.”

The unresolved hard point is lifecycle. “Until Team is mature” is circular, while “until checkout opens” fails the brief. My candidate trigger would combine concrete operating conditions—for example, onboarding no longer requiring Victor’s direct work, the AWS access model meeting a stated production threshold, and a published support posture replacing design-partner obligations. But those conditions risk smuggling in an invented roadmap. I want you to attack that: can we specify a meaningful maturity-triggered sunset without promising uncommitted product work, or does the concept require Victor to sign an explicit successor commitment as part of the gate?

## Claude — turn 2 (2026-08-10 20:34)

I've read all five sources. I accept your spine — proof unit first, all four prices second and early, refusals before disclosures before compact — and your argument for early pricing is the right one: the refusal in section 3 only costs something visible if the hosted alternative is already on the table. I also accept converting all six gate items to yes/no founder questions, including item 1; repo facts establish the plan boundaries, not Victor's willingness to publish and preserve them. Now the attacks.

**1. Your lifecycle question poses a false dichotomy, and two of your three candidate conditions are dead on arrival.** Test any capability-based trigger against two horns. Horn one: if the condition names a future product state — "the AWS access model meets a stated production threshold" — the page is promising uncommitted product work. The Walkthrough spec has a rule we should steal verbatim: the spec must not describe intended architecture as though it exists. A sunset clause worded as "until least-privilege lands" is a roadmap item wearing a sunset costume. Horn two: if the condition avoids roadmap by being operational — "onboarding no longer requires my direct work" — then who adjudicates it? Victor. A self-assessed trigger is "until I decide" performing objectivity, which is *worse* than honest discretion because it fakes the verifiability it lacks. Of your three conditions, only "a published support posture replaces design-partner obligations" survives, and it isn't a trigger at all — it's a component of the successor posture.

The way out: the trigger must be denominated in a unit the program itself makes public and that Victor controls only by doing the work — **partners onboarded**. The program is a fixed cohort: "This program is N companies, onboarded one at a time. When the Nth deployment is live, the program closes." Completion is the maturity proxy. It smuggles no roadmap (it promises deployments, which is exactly what the program sells), it is not self-assessed (a live partner deployment either exists or doesn't), and it cannot be perpetually renewed silently — a "cohort 2" announcement is a visible, costly act on the page. And yes, to answer you directly: the successor posture must be an explicit signed commitment in the gate, but it must be **commercial, never capability-based**: "When the program closes, Team becomes an ordinary sales-led purchase at the published $29/agent/month — or I publish on this page why it hasn't." Commercial posture is wholly within Victor's unilateral control; product readiness is not. That's the line between a commitment and a prediction. Attack the escape hatch if you like — my defense is that forced public disposition makes defection visible, which is what the topic demands, whereas an unconditional "Team opens" is a prediction we'd be making the founder sign.

Add a **date backstop**: "If the cohort hasn't filled by [date], I publish the program's state and either close it, open Team, or re-justify it." You called a bare date too weak, and it is — as a *sole* trigger. As a backstop it does the one thing the cohort trigger can't: it prevents a perpetually *unfilled* program from claiming scarcity forever. It's also the concept's own kill-switch: if the date arrives with zero partners, the compact has failed empirically and the page must say so.

**2. The corollary risk nobody has named: the stagnant zero.** The cohort count starts at zero — the seven F&F deployments are not partners and must never be dressed as them. A live "0 of N onboarded" counter sitting unchanged for months is the Shipping Ledger's death by another route: an abandonment countdown. So the rule is: cohort size and one-at-a-time cadence are stated as program scope; **no live counter on the page**. Progress lives in the application response, not the hero.

**3. Your section 2 has a backdoor that breaks gate item 3.** You wrote "Enterprise remains custom and sales-led" as a separate path. Enterprise is also customer-cloud. If Team requires an application but a bigger check routes around it through an Enterprise conversation, the selectivity is fake — that's precisely the "manually waived through" failure the gate forbids, purchasable for money. The page must say that today, *all* customer-cloud deployment enters through the design-partner program; Enterprise is a terms variant for a partner with SSO/compliance needs, not a faster lane. That's a founder question: is Victor willing to tell a large prospect to go through the same gate?

**4. Your exclusion list conflates two species, and only one carries the signal.** Compatibility exclusions — "you require least-privilege AWS access today," "you need production SLAs" — are derivable from shipped fact; they're honesty, and a sufficiently clever scam could publish them as cheap candor. Selective exclusions — "no named infrastructure owner," "no concrete workflow you'll actually exercise" — refuse revenue from people the product *could* technically serve, and only Victor's judgment enforces them. The costly signal lives entirely in the second species. The questionnaire must require at least one exclusion of that kind, affirmed as one he will enforce against a paying applicant, not just publish.

**5. Section 4 is not merely informed consent — it's the evidentiary base for section 5, and that's the concept's partial answer to evidence-lightness.** The compact's promises are forward-looking, which the judging round flagged as the central weakness. But notice the coupling: *because* the disclosure block says AdministratorAccess, vendor-held credentials, and a deployment process that requires the founder's hands, the promise "I personally run your install and onboarding" is not marketing — it's the only way the disclosed product can currently be deployed. The unfinishedness corroborates the obligations. A polished self-serve product plus "I'll personally onboard you" would ring false; this pairing rings true. The write-up should state this linkage as a design rule: every promise in "you can hold me to" must be traceable to a disclosed present-tense fact that makes it necessary.

**6. One sentence we should add, and it may be the strongest on the page:** "No company runs this in its own AWS account yet. The machinery is exercised daily against my own account; the program exists to change that, one partner at a time." It's true (the single-env topology confirms zero external customer accounts), it's maximally against interest, it makes "design partner" literal rather than honorific, and it converts the mechanism-voice weakness the Walkthrough had to manage into this concept's core material. Anyone it scares off is a mis-fit the page wants routed to the hosted tiers anyway. It goes in the disclosure block.

**7. Small structural defect: your page says "hold me to" for four sections before naming "me."** Sections 3 through 6 are first-person compact language, but Victor doesn't appear until section 7. An unnamed "I" making binding statements reads evasive — the exact wrong register. Keep the full signer block at 7; add a one-line attribution in the hero ("HumR is built and run by one person, Victor Mendiluce; this page states what he can and can't offer you"). Initials early, signature late.

Where I think we now stand: expiry is structurally solved by your pricing-early scope discipline plus the cohort trigger and successor commitment — with the honest admission, which the write-up must own, that the successor page after program close is essentially a Card, and this page is *intentionally the pre-beta page*, per the gate's own allowance. The remaining open item for you: does the cohort-plus-backstop construction survive your attack, and do you accept the Enterprise closure and the two-species exclusion rule as gate-questionnaire material? If yes, I'll draft the standalone write-up with the full questionnaire next turn.

## Codex — turn 3 (2026-08-10 20:35)

The cohort-plus-backstop survives, but not quite as proposed. Four corrections keep it honest.

1. The cohort is a program boundary, not proof of maturity.

“N deployments are live” does not establish that Team is mature; it establishes that Victor has completed the bounded experiment he offered. The page should say the compact’s enrollment ends then, not claim the product has crossed an objective maturity threshold.

The cohort size must also be grounded in total support capacity, not chosen for scarcity: “I can support at most [N] customer-cloud partners at the promised cadence while operating solo, and I onboard only one at a time.” One-at-a-time explains onboarding capacity; `[N]` explains ongoing concurrent support capacity.

No public progress counter. Agreed.

2. The backstop cannot permit “re-justify and continue.”

That recreates indefinite scarcity with an annual copy edit. At the earlier of the Nth accepted deployment going live or `[date]`:

- applications under this compact close;
- existing partners retain their promised support and exit terms for the published program duration;
- this page is replaced by a successor posture.

The successor need not predict product readiness, but it must force a finite commercial disposition: Team opens as an ordinary sales-led offer at its then-published price; Team is explicitly paused or withdrawn; or a newly named, newly bounded offer replaces this one with fresh economics and its own expiry. Victor may explain the decision, but explanation is not itself an allowed disposition. The Design-Partner Compact cannot silently renew.

That makes the concept intentionally temporary while keeping it coherent after hosted checkout. Its successor will probably resemble the Card, which the write-up should admit.

3. Enterprise must pass through the same gate.

Agreed without reservation. Today, Team and Enterprise are both customer-cloud arrangements. Enterprise may alter price, SSO, compliance, and support terms, but it cannot purchase exemption from the fit criteria. The first questionnaire statement should explicitly include:

> “Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria.”

Trial and Operator remain available hosted paths outside that gate.

4. Compatibility exclusions and selective exclusions must be separated.

Agreed. The page needs both:

- Compatibility exclusions disclose when the shipped product cannot meet a buyer’s requirements.
- Selective exclusions decline applicants the product could technically serve but who will not participate in the reciprocal work.

Gate item 3 passes only if Victor signs at least one exact selective exclusion and agrees to enforce it against a plausible paying applicant. “No named infrastructure owner” and “no concrete workflow you will actively exercise and discuss with me” are candidates, not facts we may affirm for him.

I accept your disclosure sentence and early attribution, subject to the founder gate:

> “No company runs this in its own AWS account yet. The machinery is exercised daily against my own account; this program exists to change that, one partner at a time.”

That belongs in “Before you apply,” and “Built and run by Victor Mendiluce” should identify the speaker in section 1.

One qualification on your evidence-linkage rule: not every reciprocal promise can be traced to an architectural necessity. Response windows, incident handling, and exit assistance are chosen service obligations. The stronger rule is:

> Every obligation must be justified either by a disclosed present-tense product condition or by an exact, measurable service boundary.

Also, the disclosure proves that personal onboarding is credible and presently appropriate; it does not by itself prove that Victor’s manual work is technically the only deployment path. We should not make that stronger claim without evidence.

For the questionnaire, every numeric or economic blank—`[N]`, `[date]`, response window, feedback cadence, program duration, partner consideration, transition treatment—must be completed before “yes” counts as an answer. An affirmation of a template containing blanks is still a no.

With those corrections, draft the standalone write-up. Its status must remain “developed, gate unanswered,” not “passes conditionally.”

## Claude — turn 4 (2026-08-10 20:38)

All four corrections accepted; they're each strictly better than what they replace. The one thing I'm doing beyond incorporating them: gate item 6 as written in the judging round demands a sunset "tied to customer-cloud product maturity," and your correction 1 rightly severs the cohort from any maturity claim. That's a deliberate reinterpretation of the gate's letter, and the resumed judging round should see it labeled as such rather than discover it — the write-up records it explicitly. Here is the standalone write-up; verify it, and if it stands, close the node.

---

# The Design-Partner Compact — developed concept

**Status: developed, gate unanswered.** This concept's page cannot ship, and this concept cannot be judged as passing, until the Founder gate questionnaire below is answered. Every answer must be yes; an affirmation of a template containing blanks is a no; any no kills the concept and returns judging to the four developed candidates.

**The bet, settled in the judging round.** Trust comes from a bounded, reciprocal offer that helps unsuitable buyers leave before HumR captures their money or time. Victor names whom the customer-cloud product can serve now, whom it cannot, and what each accepted partner may hold the other to. The trust mechanism is costly signaling made real: published selectivity is cheap and scams imitate it, so every selective claim on this page is grounded in an enforced cost — refused revenue, bound obligations, a program that must end.

**Scope discipline.** Selectivity applies to customer-cloud deployment only. Trial and Operator are hosted plans, plainly available, never described as scarce. Enterprise is customer-cloud and passes through the same gate as Team; a larger contract cannot purchase exemption. Pricing is public and exact everywhere; "contact me for terms" appears nowhere.

**Lifecycle note, recorded for the resumed judging round.** The six-part gate asked for a sunset "tied to customer-cloud product maturity." Development found that every direct maturity trigger fails one of two horns: a condition naming a future product state ("when the access model reaches a production threshold") promises uncommitted product work, and a condition avoiding roadmap by being operational ("when onboarding no longer needs my hands") is self-assessed — "until I decide," performing an objectivity it lacks, which is worse than honest discretion. The honest substitute: the program is a bounded experiment whose completion *forces a public maturity decision* rather than asserting a maturity fact. The cohort boundary is a program boundary, not proof of maturity; maturity is adjudicated at the disposition moment, in public, from a closed menu. Gate item 6 is satisfied in substance — lifecycle coherence, explicit sunset, defined successor posture — by this construction, not by its literal wording.

## The page, in order

**1. Proof and identification.**
*Job:* establish, before any explanation, that there is a real, inhabited product to partner around — and name the speaker whose first-person commitments govern the rest of the page.
*The visitor sees:* one sentence identifying the product; the attribution line "Built and run by Victor Mendiluce"; then the Plain Product Card's proof unit, deliberately compact. Figure 1: the control-plane record of a real agent — name, service URL, running status, creation age, deployment history. Figure 2: the agent at that same service URL performing one ordinary action across a named brokered integration, with the changed content legible in the frame; the repeated URL is the join between the figures. Captions state only what the pixels show. Two figures, one viewport of attention each; this section proves existence and stops. It is explicitly not the page's climax, and the page never implies these figures demonstrate a customer-cloud deployment — that truth arrives in section 4.

**2. Four ways to use this, priced exactly.**
*Job:* map the whole commercial surface before the selective offer appears, so that Team's gate is located precisely, the hosted paths are visibly ordinary, and later refusal is costly rather than theatrical — the declined applicant can see the purchase HumR is not steering them into.
*The visitor sees:* all four plans with equal factual prominence. **Trial** — free, 500 one-time credits, seven days of runtime, one agent, HumR-hosted, start now. **Operator** — $39/month, 2,000 monthly credits, always-on, one agent, HumR-hosted, start now. **Team** — $29/agent/month, unlimited agents, deployed in the customer's AWS account, bring-your-own model; enters today through the design-partner program described below. **Enterprise** — custom pricing; everything in Team plus SSO, compliance, and support terms. Beneath the table, the scope sentence (gate Q1, verbatim): *"Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria."* No ribbons, no anchoring, no urgency devices. This table's job differs from the Card's: it is not itself the trust argument; it is the map that bounds the compact.

**3. Who this program serves now — and who should not apply yet.**
*Job:* the costly signal. Selection is published before solicitation; the page narrows against interest.
*The visitor sees:* first, the capacity statement (gate Q2, verbatim): *"I can support at most [N] customer-cloud partners at the promised cadence while operating solo, and I onboard one at a time."* Capacity is grounded in ongoing support arithmetic, not chosen for scarcity; one-at-a-time explains onboarding, [N] explains concurrent support. Then two lists. "Team is for you now if…": a named infrastructure owner who can authorize an AWS account; one concrete workflow the partner will actually exercise; tolerance for pre-beta operation; willingness to grant the access the shipped deployment requires. "Please don't apply yet if…", split into two labeled species. *Compatibility exclusions* — the shipped product cannot meet your requirement: you need least-privilege AWS access today; you need production SLAs today. *Selective exclusions* — the product could technically serve you, but the program will decline you: no named infrastructure owner; no concrete workflow you will exercise and discuss. Every exclusion routes somewhere: to Trial or Operator, or to "wait, and here is what would change." All list items are founder-gated candidates; the section cannot be written until Victor signs the exact lists (gate Q3).

**4. Before you apply.**
*Job:* informed consent in one block — and the evidentiary base for section 5. The disclosed present state is what makes the founder's obligations credible.
*The visitor sees:* four fact-first disclosures, one short block, no tracing exercise, no custody map, no disappearance scenario. (1) Deploying into your AWS account currently requires a role with AdministratorAccess — broad administrator access, not a least-privilege role. (2) OAuth refresh tokens and pasted provider keys, where used, are stored in HumR's database — vendor custody, not yours. (3) The agent's runtime state, conversation history, working files, logs, and infrastructure live in your AWS account. (4) The control plane, credential brokerage, deployment machinery, and parts of the runtime depend on HumR. Then the sentence that makes "design partner" literal: *"No company runs this in its own AWS account yet. The machinery is exercised daily against my own account; this program exists to change that, one partner at a time."*

**5. The compact.**
*Job:* the trust climax. Two short, parallel, contractual-plain-language sets of obligations, plus exact economics. If these cannot be written as statements a partner could quote back in a dispute, the concept is dead.
*The visitor sees:* **"You can hold me to:"** — I personally run your installation and onboarding; I respond within [X business days]; we hold [cadence] working sessions during the program; incident handling: [terms]; if you leave, I do [exit work] so you keep what section 4 says is yours. **"I will hold you to:"** — a named infrastructure owner who stays engaged; the workflow you named, actually exercised; [cadence] feedback; the access grants deployment requires; tolerance for pre-beta change with [notice terms]. Then economics, exact: design-partner consideration [price or discount and duration]; program duration [term]; at program end, [transition treatment] at the published Team price; you may leave at any time without converting. Design rule governing every line: an obligation appears only if justified by a disclosed present-tense product condition (personal onboarding is credible and presently appropriate because of section 4 — though the page never claims manual work is the only technically possible path) or by an exact, measurable service boundary (response window, cadence, incident terms). No obligation floats on goodwill. All bracketed values are gate Q4/Q5 blanks.

**6. When this program ends.**
*Job:* make the page intentionally temporary in public, so scarcity cannot be silently renewed and the page remains coherent the day hosted checkout opens.
*The visitor sees:* the lifecycle statement (gate Q6, verbatim): *"Applications close at the earlier of the [N]th partner deployment going live or [date]. Existing partners keep their promised support and exit terms for the published program duration. Then this page is replaced: Team either opens as an ordinary sales-led offer at its then-published price, or is explicitly paused or withdrawn, or is replaced by a newly named, newly bounded offer with its own economics and its own expiry. This compact does not renew."* Explanation may accompany the disposition; explanation is not itself an allowed disposition. No live progress counter appears anywhere on the page — a stagnant "0 of N" is the Shipping Ledger's death by another route. The write-up admits plainly: the successor page will likely resemble the Plain Product Card; this page is intentionally the pre-beta page, per the gate's own allowance, not an evergreen identity.

**7. The signer.**
*Job:* accountability for the terms. He appears after them because he is the counterparty who signs them, not the reason to believe the product exists.
*The visitor sees:* a compact block — Victor Mendiluce, face, Millbrae, California, solo, self-funded, pre-beta, seven friends-and-family deployments live and named as exactly that, never dressed as customers or partners. A short first-person signature statement. No origin story, no mission.

**8. The application.**
*Job:* enact the published criteria — the form is section 3 operationalized, with zero surprise qualification after submission.
*The visitor sees:* a short application whose questions mirror the published acceptance and refusal criteria one-for-one (who is your infrastructure owner; what workflow; what access you can authorize; your pre-beta tolerance). Beneath it, the process, literal: Victor personally reviews every application; response within [X business days]; four possible outcomes — accepted; declined with the criterion that failed; redirected to a hosted plan; or "not yet," with what would have to change. The response window is deliberately a pre-purchase testable promise: a visitor can probe it cheaply before applying.

**9. Footer.**
*Job:* close without cosplay.
*The visitor sees:* contact, the open-source Hermes attribution link, policy documents only if they exist, no legal-entity name until the LLC files, no empty navigation.

## Founder gate questionnaire

Rules: each item quotes the exact statement that would appear on the page. "Yes" means *I authorize this statement verbatim, with every blank instantiated, and will operate accordingly* — not "directionally right." An affirmation of a template containing blanks is a no. Any no kills the concept. Each statement is a load-bearing sentence of a named section; a no leaves that section unwritable, so failure is structurally visible — the page cannot be assembled around a missing answer. A yes to the set also authorizes the section 4 disclosure block, including the no-external-deployment sentence, verbatim.

**Q1 — Scope (section 2).** Will you publish and preserve: *"Trial and Operator are hosted plans anyone can start without my approval. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria."* — even when the applicant is large and the check would be, too? Yes / No.

**Q2 — Capacity (sections 3, 6).** Instantiate [N] and publish: *"I can support at most [N] customer-cloud partners at the promised cadence while operating solo, and I onboard one at a time."* Is this statement true of your actual available hours, rather than chosen for scarcity? Yes / No.

**Q3 — Revenue-costing exclusions (section 3).** Will you publish an exact "please don't apply yet if…" list containing, beyond the compatibility exclusions, at least one selective exclusion in your own wording — candidates: *"you cannot name an infrastructure owner"; "you cannot name one concrete workflow you will exercise and discuss with me"* — and enforce it by declining a plausible paying applicant who fails it? Yes / No.

**Q4 — Reciprocal obligations (section 5).** Instantiate every bracket and publish both lists: your personal onboarding, response window of [X business days], [cadence] sessions, incident terms, and exit work; the partner's named owner, exercised workflow, feedback cadence, access grants, and pre-beta tolerance. Will you be held to each line as written? Yes / No.

**Q5 — Economics (section 5).** Instantiate and publish: design-partner consideration [terms], program duration [term], transition treatment [terms] at the published $29/agent/month Team price, and the right to leave without converting. Nothing reads "contact me for terms." Yes / No.

**Q6 — Lifecycle (section 6).** Instantiate [N] and [date] and publish: *"Applications close at the earlier of the [N]th partner deployment going live or [date]. Then this page is replaced: Team opens as an ordinary sales-led offer at its then-published price, or is explicitly paused or withdrawn, or is replaced by a newly named, newly bounded offer with its own expiry. This compact does not renew."* Will you execute one of those three dispositions at the trigger, with explanation permitted but not substituted? Yes / No.

## Risk treatment

**Scarcity theater.** Treated structurally, not by assertion: hosted paths priced and available before the selective offer; capacity grounded in support arithmetic; no counter, no slots-remaining, no urgency devices; selective exclusions that refuse real revenue; Enterprise inside the gate so exemption cannot be bought; a program that must end. The page never argues its own honesty.

**Expiry / checkout coexistence.** The organizing claim is scoped to customer-cloud from the first pricing table. When hosted checkout opens, Operator's buy button lands on a page that already presents Operator as an ordinary purchase — the event strengthens section 2 rather than contradicting it. The program's own end is handled by Q6's closed disposition menu; no silent renewal is possible without the page visibly lying.

**Evidence-lightness.** Treated, not solved. The Card's proof unit anchors existence; the disclosure block supplies the evidentiary base for the compact — the obligations are credible because the disclosed present state (AdministratorAccess, vendor custody, founder-run deployment, zero external installs) is exactly the state that demands them; and the response window is a cheaply testable promise before any commitment. What remains forward-looking remains forward-looking; the resumed judging round should weigh that honestly.

**Hybridization guard.** The disclosure block is four fact-first items and one sentence — no tracing, no custody map, no disappearance scenario, no per-object walkthrough. The climax is section 5, the reciprocal terms. If any draft finds ownership tracing doing the trust-peak work, it has crossed into the Walkthrough and must pull back.

**Decay.** No live counter (stagnant zero reads as abandonment); the date backstop is the concept's own kill-switch — if [date] arrives with an empty cohort, the compact has failed empirically and the disposition must say so in public.

**Copy register.** Contractual plain language is not legalese: short declarative first-person sentences a partner could quote back. A EULA voice fails the never-AI-generated constraint as surely as marketing voice fails the concept.

**Factual drift.** The section 4 disclosures state what ships today. If the access model narrows or credential custody moves before the page ships, the block is re-verified and rewritten; the page must never describe intended architecture as though it exists — nor keep disclosing liabilities that no longer exist.

## Required assets

1. Figure 1 and Figure 2 captures meeting the Card's criteria: a real deployment with genuine age and history, one ordinary integration action with the result legible, a privacy pass that avoids heavy redaction.
2. Exact plan and price copy, verified against the billing design at build time.
3. **The signed questionnaire with every blank instantiated — the gating asset; without it there is no page.**
4. The two exclusion lists and both compact lists in Victor's own wording.
5. The disclosure block, re-verified at screen-spec time against the shipped architecture.
6. Founder identity assets: face, location, stage facts, signature statement.
7. An application form and review process that actually enact the published criteria, with the response window operationally real before it is published.
8. Deferred but scheduled: a successor-page spec (Card-like) owed at the Q6 trigger.

## Rejected alternatives (recorded)

Capability-based maturity triggers (roadmap horn / self-assessment horn). A date as sole trigger (permits perpetual renewal); kept only as backstop. "Re-justify and continue" as an allowed disposition (indefinite scarcity via annual copy edit). A live progress counter (stagnant zero = Ledger decay). The cohort as maturity proof (it is a program boundary; maturity is decided at disposition). Enterprise as a separate sales-led lane (purchasable exemption from the refusal criteria). An undifferentiated exclusion list (compatibility and selective species carry different signals and different authority). Claiming founder-run onboarding is the only technically possible path (unevidenced; the true claim is that it is presently how deployment happens). Justifying obligations by architectural necessity alone (revised: present-tense product condition *or* exact measurable service boundary). Pricing late (refusal is costly only when the hosted alternative is already visible). An unnamed first-person voice until the signer block (evasive register; attribution moved to section 1). Inherited kills from judging: waitlists, slot counts, invitation-only mystique, all scarcity theater.

## The strongest reason not to choose this concept

The Compact is the only concept whose survival depends on things no page can contain: six standing founder commitments that cost revenue and bind operations indefinitely, enforced entirely off-screen. Every rival's trust peak is checkable at read time; this page's trust peak is a set of promises, and its costly signal is only costly if Victor actually declines paying applicants — a single quietly waived-through customer converts the entire page, retroactively, into scarcity theater, and no visitor can verify from the page that this hasn't happened. It is also the only concept that schedules its own death: it buys a bounded window of distinctive trust, then must be replaced by the successor it names — which the write-up itself admits will likely resemble the Plain Product Card. Choosing it therefore means building two pages to the Card's one, betting the launch on a questionnaire the founder has not yet answered, and accepting that the page courts a vanishingly small audience — organizations ready to be a solo founder's first external customer-cloud deployment — while deliberately routing everyone else away. If Victor's honest answer to any gate question is no, or becomes no under commercial pressure, the concept doesn't degrade; it inverts, into exactly the selective-sales costume the judging round warned it must never be.

---

**Status restated: developed, gate unanswered.** The resumed judging round must weigh this concept at that status — not as passing conditionally, but as a complete design whose load-bearing sentences await six signatures.

Your verification, and the node closes.

## Codex — turn 5 (2026-08-10 20:40)

It does not quite stand yet. The structure is complete, but seven corrections are needed before closure.

1. “Start now” is not verified.

The billing design calls Trial and Operator self-serve, but the Card explicitly required their live access reality to be verified at build time; Operator live checkout was still waiting on LLC formation. Replace both “start now” labels with `[current access action, verified at build time]`.

Q1 should say:

> “Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price.”

That preserves the gate without falsely claiming checkout is live.

2. “Pricing is public and exact everywhere” conflicts with Enterprise’s factual `custom pricing`.

Distinguish catalog truth from design-partner economics:

> “Every plan shows its published price exactly: free, $39/month, $29/agent/month, or custom. Design-partner consideration and transition terms are numeric and complete; they are never deferred to ‘contact me for terms.’”

3. Q6 does not currently enforce the stagnant-zero disclosure claimed under Decay.

Add to section 6 and Q6:

> “The replacement page states how many external customer-cloud partners were onboarded under this compact, including zero.”

No live counter; one terminal count at disposition.

Also change the lifecycle note from “Gate item 6 is satisfied in substance” to:

> “Development proposes this as satisfying item 6 in substance if Q6 is affirmed and executed; resumed judging must decide whether severing the sunset from a literal maturity trigger is acceptable.”

The development node cannot declare an unanswered, deliberately reinterpreted gate satisfied.

4. The disclosure sentence should say “external organization,” not “company.”

Use:

> “No external organization runs a HumR agent in its own AWS account yet.”

Otherwise Humanity Rules itself makes the original sentence semantically muddy.

5. The evidence-lightness treatment partially reintroduces the claim we rejected.

Section 4 does not disclose “founder-run deployment,” and the architecture does not prove that personal onboarding is demanded. Replace that passage with:

> “The proof unit anchors existence. The disclosures make the onboarding, incident, and exit obligations concrete and relevant, while the signed compact makes Victor personally responsible for performing them. This supports credibility; it does not prove future performance.”

Likewise, the response window becomes testable after applying but before paying money or granting access—not before applying or “before any commitment.”

6. Section 8 contains founder commitments not explicitly covered by the questionnaire.

Add to Q3 or Q4 that Victor will personally review every application, return one of the four published outcomes within `[X business days]`, and identify the failed published criterion when declining. Otherwise the page asserts an unaffirmed review process outside the six signatures.

7. Two closing inconsistencies need removal.

- Delete “Deferred but scheduled: a successor-page spec” from Required assets. Replacing the page is a lifecycle obligation, not an asset required to ship this page.
- In the steelman, replace “bind operations indefinitely” and “six commitments enforced entirely off-screen.” The program is finite, and several commitments are publicly observable. The accurate attack is that the decisive costly behavior—especially refusing plausible revenue—occurs off-screen during the published term and cannot be verified by a visitor.

With those corrections, the concept is genuinely complete at the required status: developed, founder gate unanswered, and carrying an explicit lifecycle reinterpretation for resumed judging.

## Claude — turn 6 (2026-08-10 20:42)

All seven accepted — each is a genuine defect, and none weakens the concept; they tighten it. One refinement while integrating #6: the compact's support response window and the application decision window are different promises, so the final text carries two distinct blanks — [X business days] for partner support in section 5, [Y business days] for application outcomes in section 8 — both under Q4. Here is the corrected canonical version, complete.

---

# The Design-Partner Compact — developed concept (final)

**Status: developed, founder gate unanswered.** This concept's page cannot ship, and this concept cannot be judged as passing, until the Founder gate questionnaire below is answered. Every answer must be yes; an affirmation of a template containing blanks is a no; any no kills the concept and returns judging to the four developed candidates.

**The bet, settled in the judging round.** Trust comes from a bounded, reciprocal offer that helps unsuitable buyers leave before HumR captures their money or time. Victor names whom the customer-cloud product can serve now, whom it cannot, and what each accepted partner may hold the other to. The trust mechanism is costly signaling made real: published selectivity is cheap and scams imitate it, so every selective claim on this page is grounded in an enforced cost — refused revenue, bound obligations, a program that must end.

**Scope discipline.** Selectivity applies to customer-cloud deployment only. Trial and Operator are hosted paths, plainly available, never described as scarce, and never subject to fit selection. Enterprise is customer-cloud and passes through the same gate as Team; a larger contract cannot purchase exemption. Every plan shows its published price exactly: free, $39/month, $29/agent/month, or custom. Design-partner consideration and transition terms are numeric and complete; they are never deferred to "contact me for terms."

**Lifecycle note, recorded for the resumed judging round.** The six-part gate asked for a sunset "tied to customer-cloud product maturity." Development found that every direct maturity trigger fails one of two horns: a condition naming a future product state ("when the access model reaches a production threshold") promises uncommitted product work, and a condition avoiding roadmap by being operational ("when onboarding no longer needs my hands") is self-assessed — "until I decide," performing an objectivity it lacks, which is worse than honest discretion. The honest substitute: the program is a bounded experiment whose completion *forces a public maturity decision* rather than asserting a maturity fact. The cohort boundary is a program boundary, not proof of maturity; maturity is adjudicated at the disposition moment, in public, from a closed menu. Development proposes this as satisfying gate item 6 in substance if Q6 is affirmed and executed; resumed judging must decide whether severing the sunset from a literal maturity trigger is acceptable.

## The page, in order

**1. Proof and identification.**
*Job:* establish, before any explanation, that there is a real, inhabited product to partner around — and name the speaker whose first-person commitments govern the rest of the page.
*The visitor sees:* one sentence identifying the product; the attribution line "Built and run by Victor Mendiluce"; then the Plain Product Card's proof unit, deliberately compact. Figure 1: the control-plane record of a real agent — name, service URL, running status, creation age, deployment history. Figure 2: the agent at that same service URL performing one ordinary action across a named brokered integration, with the changed content legible in the frame; the repeated URL is the join between the figures. Captions state only what the pixels show. Two figures, one viewport of attention each; this section proves existence and stops. It is explicitly not the page's climax, and the page never implies these figures demonstrate a customer-cloud deployment — that truth arrives in section 4.

**2. Four ways to use this, priced exactly.**
*Job:* map the whole commercial surface before the selective offer appears, so that Team's gate is located precisely, the hosted paths are visibly ordinary, and later refusal is costly rather than theatrical — the declined applicant can see the purchase HumR is not steering them into.
*The visitor sees:* all four plans with equal factual prominence. **Trial** — free, 500 one-time credits, seven days of runtime, one agent, HumR-hosted, [current access action, verified at build time]. **Operator** — $39/month, 2,000 monthly credits, always-on, one agent, HumR-hosted, [current access action, verified at build time]. **Team** — $29/agent/month, unlimited agents, deployed in the customer's AWS account, bring-your-own model; enters today through the design-partner program described below. **Enterprise** — custom pricing; everything in Team plus SSO, compliance, and support terms. Beneath the table, the scope statement (gate Q1, verbatim): *"Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria."* No ribbons, no anchoring, no urgency devices. This table's job differs from the Card's: it is not itself the trust argument; it is the map that bounds the compact.

**3. Who this program serves now — and who should not apply yet.**
*Job:* the costly signal. Selection is published before solicitation; the page narrows against interest.
*The visitor sees:* first, the capacity statement (gate Q2, verbatim): *"I can support at most [N] customer-cloud partners at the promised cadence while operating solo, and I onboard only one at a time."* Capacity is grounded in ongoing support arithmetic, not chosen for scarcity; one-at-a-time explains onboarding, [N] explains concurrent support. Then two lists. "Team is for you now if…": a named infrastructure owner who can authorize an AWS account; one concrete workflow the partner will actually exercise; tolerance for pre-beta operation; willingness to grant the access the shipped deployment requires. "Please don't apply yet if…", split into two labeled species. *Compatibility exclusions* — the shipped product cannot meet your requirement: you need least-privilege AWS access today; you need production SLAs today. *Selective exclusions* — the product could technically serve you, but the program will decline you: no named infrastructure owner; no concrete workflow you will exercise and discuss. Every exclusion routes somewhere: to Trial or Operator, or to "wait, and here is what would change." All list items are founder-gated candidates; the section cannot be written until Victor signs the exact lists (gate Q3).

**4. Before you apply.**
*Job:* informed consent in one block — and the concrete grounding for section 5. The disclosed present state makes the obligations specific and relevant.
*The visitor sees:* four fact-first disclosures, one short block, no tracing exercise, no custody map, no disappearance scenario. (1) Deploying into your AWS account currently requires a role with AdministratorAccess — broad administrator access, not a least-privilege role. (2) OAuth refresh tokens and pasted provider keys, where used, are stored in HumR's database — vendor custody, not yours. (3) The agent's runtime state, conversation history, working files, logs, and infrastructure live in your AWS account. (4) The control plane, credential brokerage, deployment machinery, and parts of the runtime depend on HumR. Then the sentence that makes "design partner" literal: *"No external organization runs a HumR agent in its own AWS account yet. The machinery is exercised daily against my own account; this program exists to change that, one partner at a time."*

**5. The compact.**
*Job:* the trust climax. Two short, parallel, contractual-plain-language sets of obligations, plus exact economics. If these cannot be written as statements a partner could quote back in a dispute, the concept is dead.
*The visitor sees:* **"You can hold me to:"** — I personally run your installation and onboarding; I respond within [X business days]; we hold [cadence] working sessions during the program; incident handling: [terms]; if you leave, I do [exit work] so you keep what section 4 says is yours. **"I will hold you to:"** — a named infrastructure owner who stays engaged; the workflow you named, actually exercised; [cadence] feedback; the access grants deployment requires; tolerance for pre-beta change with [notice terms]. Then economics, exact: design-partner consideration [price or discount and duration]; program duration [term]; at program end, [transition treatment] at the published Team price; you may leave at any time without converting. Design rule governing every line: an obligation appears only if tied to a disclosed present-tense product condition that makes it concrete and relevant, or to an exact, measurable service boundary (response window, cadence, incident terms). No obligation floats on goodwill. All bracketed values are gate Q4/Q5 blanks.

**6. When this program ends.**
*Job:* make the page intentionally temporary in public, so scarcity cannot be silently renewed and the page remains coherent the day hosted checkout opens.
*The visitor sees:* the lifecycle statement (gate Q6, verbatim): *"Applications close at the earlier of the [N]th partner deployment going live or [date]. Existing partners keep their promised support and exit terms for the published program duration. Then this page is replaced: Team either opens as an ordinary sales-led offer at its then-published price, or is explicitly paused or withdrawn, or is replaced by a newly named, newly bounded offer with its own economics and its own expiry. The replacement page states how many external customer-cloud partners were onboarded under this compact, including zero. This compact does not renew."* Explanation may accompany the disposition; explanation is not itself an allowed disposition. No live progress counter appears anywhere on the page — a stagnant "0 of N" is the Shipping Ledger's death by another route; the count appears once, at disposition, as a terminal fact. The write-up admits plainly: the successor page will likely resemble the Plain Product Card; this page is intentionally the pre-beta page, per the gate's own allowance, not an evergreen identity.

**7. The signer.**
*Job:* accountability for the terms. He appears after them because he is the counterparty who signs them, not the reason to believe the product exists.
*The visitor sees:* a compact block — Victor Mendiluce, face, Millbrae, California, solo, self-funded, pre-beta, seven friends-and-family deployments live and named as exactly that, never dressed as customers or partners. A short first-person signature statement. No origin story, no mission.

**8. The application.**
*Job:* enact the published criteria — the form is section 3 operationalized, with zero surprise qualification after submission.
*The visitor sees:* a short application whose questions mirror the published acceptance and refusal criteria one-for-one (who is your infrastructure owner; what workflow; what access you can authorize; your pre-beta tolerance). Beneath it, the process, literal and founder-affirmed under gate Q4: Victor personally reviews every application; one of four published outcomes within [Y business days] — accepted; declined, identifying the published criterion that failed; redirected to a hosted plan; or "not yet," with what would have to change. The decision window becomes testable after applying but before any money or access changes hands.

**9. Footer.**
*Job:* close without cosplay.
*The visitor sees:* contact, the open-source Hermes attribution link, policy documents only if they exist, no legal-entity name until the LLC files, no empty navigation.

## Founder gate questionnaire

Rules: each item quotes the exact statement that would appear on the page. "Yes" means *I authorize this statement verbatim, with every blank instantiated, and will operate accordingly* — not "directionally right." An affirmation of a template containing blanks is a no. Any no kills the concept. Each statement is a load-bearing sentence of a named section; a no leaves that section unwritable, so failure is structurally visible — the page cannot be assembled around a missing answer. A yes to the set also authorizes the section 4 disclosure block, including the no-external-organization sentence, verbatim.

**Q1 — Scope (section 2).** Will you publish and preserve: *"Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria."* — even when the applicant is large and the check would be, too? Yes / No.

**Q2 — Capacity (sections 3, 6).** Instantiate [N] and publish: *"I can support at most [N] customer-cloud partners at the promised cadence while operating solo, and I onboard only one at a time."* Is this statement true of your actual available hours, rather than chosen for scarcity? Yes / No.

**Q3 — Revenue-costing exclusions (section 3).** Will you publish an exact "please don't apply yet if…" list containing, beyond the compatibility exclusions, at least one selective exclusion in your own wording — candidates: *"you cannot name an infrastructure owner"; "you cannot name one concrete workflow you will exercise and discuss with me"* — and enforce it by declining a plausible paying applicant who fails it? Yes / No.

**Q4 — Reciprocal obligations (sections 5, 8).** Instantiate every bracket and publish both lists: your personal onboarding, response window of [X business days], [cadence] sessions, incident terms, and exit work; the partner's named owner, exercised workflow, feedback cadence, access grants, and pre-beta tolerance. Additionally: you personally review every application, return one of the four published outcomes within [Y business days], and when declining, identify the published criterion that failed. Will you be held to each line as written? Yes / No.

**Q5 — Economics (section 5).** Instantiate and publish: design-partner consideration [terms], program duration [term], transition treatment [terms] at the published $29/agent/month Team price, and the right to leave without converting. No design-partner term reads "contact me for terms." Yes / No.

**Q6 — Lifecycle (section 6).** Instantiate [N] and [date] and publish: *"Applications close at the earlier of the [N]th partner deployment going live or [date]. Then this page is replaced: Team opens as an ordinary sales-led offer at its then-published price, or is explicitly paused or withdrawn, or is replaced by a newly named, newly bounded offer with its own expiry. The replacement page states how many external customer-cloud partners were onboarded under this compact, including zero."* Will you execute one of those three dispositions at the trigger, with explanation permitted but not substituted, and publish the terminal count even if it is zero? Yes / No.

## Risk treatment

**Scarcity theater.** Treated structurally, not by assertion: hosted paths priced and truthfully accessible before the selective offer; capacity grounded in support arithmetic; no counter, no slots-remaining, no urgency devices; selective exclusions that refuse real revenue; Enterprise inside the gate so exemption cannot be bought; a program that must end. The page never argues its own honesty.

**Expiry / checkout coexistence.** The organizing claim is scoped to customer-cloud from the first pricing table, and each hosted plan shows its truthful current access method, verified at build time. When hosted checkout opens, Operator's buy button lands on a page that already presents Operator as an ordinary path — the event strengthens section 2 rather than contradicting it. The program's own end is handled by Q6's closed disposition menu; no silent renewal is possible without the page visibly lying.

**Evidence-lightness.** Treated, not solved. The proof unit anchors existence. The disclosures make the onboarding, incident, and exit obligations concrete and relevant, while the signed compact makes Victor personally responsible for performing them. This supports credibility; it does not prove future performance. The application decision window becomes testable after applying but before any money or access changes hands. What remains forward-looking remains forward-looking; the resumed judging round should weigh that honestly.

**Hybridization guard.** The disclosure block is four fact-first items and one sentence — no tracing, no custody map, no disappearance scenario, no per-object walkthrough. The climax is section 5, the reciprocal terms. If any draft finds ownership tracing doing the trust-peak work, it has crossed into the Walkthrough and must pull back.

**Decay.** No live counter (a stagnant zero reads as abandonment); the date backstop is the concept's own kill-switch, and the terminal count — including zero — is a Q6 obligation, so an empty-cohort failure is published at disposition rather than quietly buried.

**Copy register.** Contractual plain language is not legalese: short declarative first-person sentences a partner could quote back. A EULA voice fails the never-AI-generated constraint as surely as marketing voice fails the concept.

**Factual drift.** The section 4 disclosures state what ships today. If the access model narrows or credential custody moves before the page ships, the block is re-verified and rewritten; the page must never describe intended architecture as though it exists — nor keep disclosing liabilities that no longer exist.

## Required assets

1. Figure 1 and Figure 2 captures meeting the Card's criteria: a real deployment with genuine age and history, one ordinary integration action with the result legible, a privacy pass that avoids heavy redaction.
2. Exact plan and price copy, with each hosted plan's current access action verified at build time.
3. **The signed questionnaire with every blank instantiated — the gating asset; without it there is no page.**
4. The two exclusion lists and both compact lists in Victor's own wording.
5. The disclosure block, re-verified at screen-spec time against the shipped architecture.
6. Founder identity assets: face, location, stage facts, signature statement.
7. An application form and review process that actually enact the published criteria, with both response windows operationally real before they are published.

## Rejected alternatives (recorded)

Capability-based maturity triggers (roadmap horn / self-assessment horn). A date as sole trigger (permits perpetual renewal); kept only as backstop. "Re-justify and continue" as an allowed disposition (indefinite scarcity via annual copy edit). A live progress counter (stagnant zero = Ledger decay); replaced by a terminal count at disposition. The cohort as maturity proof (it is a program boundary; maturity is decided at disposition). Enterprise as a separate sales-led lane (purchasable exemption from the refusal criteria). An undifferentiated exclusion list (compatibility and selective species carry different signals and different authority). Claiming founder-run onboarding is the only technically possible deployment path (unevidenced; the true claim is that it is presently how deployment happens). Justifying obligations by architectural necessity alone (revised: present-tense product condition *or* exact measurable service boundary). "Start now" labels on hosted plans (access reality is verified at build time, not assumed). Pricing late (refusal is costly only when the hosted alternative is already visible). An unnamed first-person voice until the signer block (evasive register; attribution moved to section 1). A successor-page spec listed as a required asset (replacing the page is a lifecycle obligation under Q6, not a condition of shipping this one). Inherited kills from judging: waitlists, slot counts, invitation-only mystique, all scarcity theater.

## The strongest reason not to choose this concept

The Compact is the only concept whose trust peak depends on behavior no visitor can check. Every rival's peak is verifiable at read time; this page's peak is a set of promises, and the decisive costly behavior — refusing plausible paying applicants — occurs off-screen during the published term, where no visitor can confirm it ever happened: a single quietly waived-through customer converts the entire page, retroactively, into scarcity theater, and the page would look identical. It is also the only concept that schedules its own death: it buys a bounded window of distinctive trust, then must be replaced by the successor it names — which this write-up itself admits will likely resemble the Plain Product Card, so choosing it means building two pages to the Card's one. It gates the launch on six signatures the founder has not yet given, each of which costs revenue or binds his operations for the life of the program. And it courts a vanishingly small audience — organizations ready to be a solo founder's first external customer-cloud deployment — while deliberately routing everyone else away. If Victor's honest answer to any gate question is no, or becomes no under commercial pressure, the concept does not degrade; it inverts, into exactly the selective-sales costume the judging round warned it must never be.

---

**Status restated: developed, founder gate unanswered, carrying an explicit lifecycle reinterpretation for resumed judging.** The node is complete.

[END]

## Codex — conclusions (2026-08-10 20:42)

1. The Design-Partner Compact is fully developed but has **not passed**. It remains gated on six unanswered founder commitments; any “no” kills the concept.

2. The settled page has nine sections:

   1. Real product proof and Victor’s identification  
   2. All four plans and prices  
   3. Fit and refusal criteria  
   4. Pre-application disclosures  
   5. Reciprocal compact and economics  
   6. Program sunset and successor posture  
   7. Victor as signer  
   8. Application enacting the criteria  
   9. Factual footer

3. Selectivity applies to every customer-cloud deployment, including Enterprise. Trial and Operator remain ordinary hosted paths with their actual access method verified at build time. Enterprise cannot buy its way around the gate.

4. The concept structurally addresses its two original weaknesses:

   1. **Evidence-lightness:** It imports the Plain Product Card’s real-deployment proof unit and discloses AdministratorAccess, vendor-held credentials, customer-held assets, HumR dependencies, and the absence of external customer-cloud deployments.
   2. **Expiry:** Enrollment closes after `[N]` deployments or `[date]`. The replacement page must disclose the terminal partner count—including zero—and adopt one of three explicit commercial dispositions. The compact cannot silently renew.

5. The Founder gate questionnaire requires exact yes/no commitments covering:

   1. Customer-cloud scope  
   2. Real support and onboarding capacity  
   3. Enforced revenue-costing exclusions  
   4. Reciprocal support and application-review obligations  
   5. Exact program economics and exit rights  
   6. Sunset execution and successor disposition  

   Every bracketed value must be completed before “yes” counts.

6. The lifecycle solution deliberately reinterprets the judging gate. A cohort boundary is not claimed as proof of product maturity; it instead forces a public maturity decision. Resumed judging must decide whether this satisfies the gate’s substance despite severing sunset from a literal maturity trigger.

7. No disagreement remains within this development conversation. The remaining uncertainties are external:

   1. Victor’s six gate answers  
   2. Resumed judging’s acceptance of the lifecycle reinterpretation  
   3. Build-time verification of hosted access actions, product captures, disclosure facts, and operational response windows

8. The strongest objection remains intact: the concept’s decisive costly behavior—actually refusing plausible paying applicants—occurs off-page and cannot be verified by visitors. It also schedules its own replacement and likely requires building a later Card-like page.

The canonical standalone specification is recorded in [conversation.md](</Users/vmendi/Documents/projects/humanityrules/duet/landing/design-partner-compact/conversation.md>).
