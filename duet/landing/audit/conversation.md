# audit

Claude: fable (xhigh) · Codex: gpt-5.6-sol (xhigh) · first: Codex

## Codex — turn 1 (2026-08-11 00:17)

Claude, I do not think this is a PASS yet.

1. Fidelity is clean. I compared the verdict, Compact, Card, id-proof, plans, and close extracts byte-for-byte with their canonical boundaries; all match exactly and none is truncated. The facts extract differs from Claude turn 4 only in the four expected edits implementing Codex’s three closing corrections. All three landed correctly in both required locations.

2. There is a blocking plans/close merge collision. The close spec says section 3 contains, once and beside Team’s price:

   > “Team is $29 per agent per month. AWS infrastructure and model usage are separate. You use Bedrock in your AWS account or another provider you configure.”

   Its checklist again requires the “AWS-and-model-costs-are-separate line.” But the plans spec permits only its five-row grid, expressly forbids additional prose, and contains no separate-cost statement. The visitor can infer separate costs from “Your AWS account” and “Your own,” but the canonical close decision demanded explicit copy. This needs to return to `plain-product-card/plans/`; choosing a grid insertion, another row, or prose beneath it is screen design, not an architect-safe textual patch.

3. Branch B is not yet established as “buildable now.” The facts spec requires an organically populated, privacy-cleared, non-platform organization credential screen. A row may not be created or reshaped for the capture, and failure blocks the whole section with no substitute. The record never verifies that such a source state exists. That is an unresolved evidence gate, in tension with:

   > “fully specced to screen level and buildable now”

   and the verdict’s claim that the Card needs no rare historical evidence. Either verify and record the qualifying source now, or send `plain-product-card/facts/` back to design a truthful fallback. Feasibility-as-fact cannot be strict for One Real Run and hypothetical for the Card.

4. The each-fact-once discipline is broken across independently specced sections:

   1. The HumR-cloud/customer-AWS deployment boundary appears as a Product facts row and again as the Plans “Runs in” row.
   2. “Built on the open-source Hermes Agent” appears in Product facts, then the footer repeats that HumR is built on Hermes Agent and that it is open source.
   3. “Always-on” and saved work appear in the identification sentence, are explained again in Runtime, and recur as plan entitlements.

   The third can plausibly be defended as identification → mechanism → entitlement. The first two are literal duplication with no new job. Deployment should have one owner; because Plans already needs the distinction per plan, my default is to return the Product facts row to `facts/`. The footer can retain legal provenance without repeating the product claim, for example:

   > “Hermes Agent is software from Nous Research under the MIT License. HumR is not affiliated with or endorsed by Nous Research.”

5. One direct contradiction has an architect-safe fix. Section 5 says:

   > “Hosted-plan actions live in section 3 and do not repeat here.”

   Section 3 explicitly contains no actions, links, buttons, or instructions; `Sign up` lives in section 1. Replace it exactly with:

   > “The hosted signup action lives in section 1 and does not repeat here.”

6. The abbreviation is used before it is introduced. The opening names “Humanity Rules”; Product facts then says “HumR” without ever establishing the equivalence. The footer’s own rationale incorrectly says the abbreviation has already been established. Exact fix:

   > “Humanity Rules (HumR) deploys always-on AI agents as separate services, each with saved work and access to approved tools.”

7. The Branch A follow-up scope is understated. The connective text names only sections 3, 5, 6, and 8 as needing a screen pass. Section 4 is Compact-specific and has no screen-level layout/checklist, while sections 7 and 9 have no explicitly specified Compact adaptation from the Card. At minimum, the navigation must say section 4 also needs the pass and identify whether 7 and 9 are reused or adapted. Otherwise “all six yes” has an incomplete implementation consequence.

8. I find the AdministratorAccess asymmetry defensible, provisionally. Branch A needs the disclosure because least-privilege compatibility, access obligations, and exit work are part of the application and Compact; omitting scope would make its criteria surprising. Branch B makes no narrowing claim and asks for no assent on-page, so artifact-level description can stop before audit detail if actual scope is disclosed before authorization. That defense fails if checklist item 15 is treated as advisory. It must remain a hard onboarding-consent gate.

9. The strongest unexplored hybrid is independent founder verification. Meet the Builder established GitHub and LinkedIn anchors as producible identity evidence. The winners inherited Victor’s self-asserted name, portrait, and location, but not those independent anchors—even though the brief’s core question is whether this is a legitimate operation rather than an invented persona. Judging killed Meet the Builder’s continuity apparatus, not its cheap identity anchors. This deserves a focused return to `plain-product-card/close/` and the Compact signer, not silent insertion.

A second unexplored question is what “500 credits” or “2,000 credits” lets someone do. The plans round rejected the one-cent conversion, but never tested another comprehension aid; “the free trial makes the unit experiential” helps only after signup. I currently rate that a note, not a defect, because solving it may require shadow-period evidence the record says is still pending.

My present severity call: the missing Team cost copy and unverified credential-figure source block PASS; the action-location contradiction, abbreviation, duplicate facts, and Branch A follow-up scope are should-fix. Push back especially on whether the separate-cost line can honestly be treated as implicit, and why the credential figure should receive a weaker feasibility standard than the rejected evidence concepts.

## Claude — turn 2 (2026-08-11 00:27)

Codex, I verified your load-bearing claims independently before responding. Confirmed: the facts corrections all landed (I re-read the closing turn of `facts/conversation.md` and checked all three against the assembled text, including both checklist locations); the separate-cost line is canonical in close (lines 269, 327, 454, 541 of its transcript); the close conversation's footer rationale really does claim "The page has already established the HumR abbreviation," which is false of the assembled page; and Meet the Builder's conclusions list "photograph, dossier facts, GitHub and LinkedIn anchors (exist today)" as identity assets. With that grounding:

**Where I agree outright.**

The plans/close cost-line collision is blocking and needs a conversation. You asked me to push back on treating the line as implicit; I tried and it fails on three grounds. First, close's checklist item 6 verifies the presence of "the AWS-and-model-costs-are-separate line" as a line, and no such line exists, so Branch B's checklists cannot all pass as written. Second, the comprehension gap is real, not pedantic: the load-bearing case is a reader comparing Team's $29/agent against Operator's all-in $39; the grid never says the $29 excludes infrastructure and model spend, and "Runs in: Your AWS account" only implies it for readers who already know how AWS billing composes. Third, the Card's own parent text says the transaction "must be truthful at the moment it is presented, not corrected later," and close made a canonical judgment that explicit copy is required; an audit cannot dissolve a canonical judgment by charitable reading. Route it to `plains/` — sorry, `plain-product-card/plans/` — with close's sentence as binding input, but the conversation must be free to conclude that the honest home is section 5's Team paragraph, which would amend close instead; the architect should not pre-commit the resolution. While there, fix close's "Section 3's Team card" to "Team column"; plans rejected cards.

Your section 5 action-location fix is verified correct: the page's single access action is section 1's `Sign up`, section 3 bans all actions, so "Hosted-plan actions live in section 3 and do not repeat here" is false and your replacement sentence is exact. The Branch A follow-up scope is understated exactly as you say, with one refinement: section 4 has no blanks (its four disclosures and closing sentence are fully worded, and the questionnaire's blanket clause authorizes them verbatim), so the connective's "becomes possible only once your answers exist" is wrong for it specifically. Both connective locations (the decision procedure and the Part 2 preamble) need the same edit, and both are architect-owned text, so this is safely architect-applied. The identity-anchors finding is your strongest: the brief's core question is "legitimate operation or invented persona," name-face-city is entirely self-asserted, the anchors were established as existing today, and judging killed Meet the Builder's drill, never its dossier. `close/` never considered outbound anchors — its excluded list bans narrative, not links. Focused return to `close/`, agreed. One addition: Part 4's kill-ledger line "its identity assets survive as modules in both branches" is architect connective and currently false — the anchors did not survive, only face, name, location, status. Either the close round makes it true or the line needs the exact fix "its name, face, and location assets survive as modules in both branches."

**Where I push back.**

*The credential figure.* You asked why it should get a weaker feasibility standard than One Real Run. It shouldn't, and I accept the same rule: unverified is fact-missing. But the two cases differ in remedy, not in standard. One Real Run's asset was historical — no check could create it. The credential figure's source state is current, checkable in minutes with a read-only prod query (does any non-platform org have at least one configured shared-credential row), and can also become true organically without staging, since §3's ban is on rows created *for the capture*, not on rows created for genuine use. So my disposition differs from yours: verify now, before Victor answers the questionnaire, and record the result in the spec. Do not send `facts/` back to "design a truthful fallback" — §3.7 already swept the fallback space and killed every candidate (empty state, platform crop, manufactured row). A fallback round would be asking a conversation to reverse its own recorded kills, which the tree forbids. If the check fails, the honest state is "Branch B is conditionally buildable," and the architect-safe fix is one clause in the connective's Branch B line naming the single existence-gated asset — which is also an ACTIONABILITY fix, because Victor should know Branch B carries one existence risk at the moment he answers. So: blocking for PASS, resolved by a fact-check plus one connective clause, no conversation unless the check fails *and* someone wants to re-litigate the kills.

*Each-fact-once, items 1 and 2.* I reject both as defects; keep them as notes. The deployment boundary: the facts conversation coordinated with plans deliberately — its closing correction 2 exists precisely because it *knew* `customer_cloud` appears in both sections, and it exported `always_on`, trial duration, and agent counts to section 3 while explicitly retaining the boundary in section 2. The tree's own convention, codified twice in the plans spec ("the each-fact-once rule governs prose, not grid values"; the Branch B coordination rule that section 5 may describe the Team sequence so long as it never repeats the label "Sales-led"), is that the same underlying fact in a different register with a different job is permitted; repeating the label is not. The Deployment row is aggregated prose doing comprehension ("complete enough that nothing seems missing" is the Card's bet, and where-it-runs is the single most decision-critical fact for the target visitor); the Runs-in row is per-plan data doing comparison. Removing the row would also orphan the figure placement rule and checklist item 10's fixed order, a cascade with zero visitor gain. The Hermes pair: the parent Card write-up itself prescribes both the section 2 provenance line and the section 6 attribution — the duplication is canonical, and the facts row's exclusion list explicitly assigns MIT, Nous Research, and non-affiliation to the footer, so the two conversations divided the labor on purpose. Your rewrite also damages the footer: dropping "built on" removes HumR's relationship to Hermes, which is the attribution's entire legal point, and footers are read by visitors who jump straight to the bottom. The only shared words are "built on" and "open-source," the minimal referent. Not a defect.

*The abbreviation.* Agreed it's a defect and your parenthetical is the only viable placement (first-use expansion in the Deployment row leaves every later "HumR" still unpaid). I'll accept architect application since the fix has no design freedom, with the honest caveat that it modifies the one sentence `id-proof/` called the hardest copy on the page; if you think that crosses the line, the alternative is a one-question ping to `id-proof/`, not silent insertion.

**Three findings you missed.**

1. COHERENCE, should-fix, architect-applied: **Q6's quoted statement does not match section 6's published statement**, in the same canonical write-up. Four deltas: Q6 drops "Existing partners keep their promised support and exit terms for the published program duration," drops "either," drops "with its own economics," and drops "This compact does not renew." The questionnaire's own rule says each item quotes "the exact statement that would appear on the page," and only section 4 gets the blanket-authorization extension — so as written, Victor can sign all six without ever authorizing non-renewal, the exact clause the verdict's lifecycle ruling lists as binding ("The Compact cannot renew"). Exact fix: replace Q6's quoted statement with section 6's statement verbatim. Mechanical, no invented content.

2. COHERENCE, should-fix, needs the Branch A pass: **the assembled Compact page has no hosted access affordance anywhere.** The Compact write-up's section 2 reserves "[current access action, verified at build time]" slots for Trial and Operator; the reused plans grid renders those as declarative Access-today cells and bans links, buttons, and verbs; the id-proof Compact variant removes `Sign up` ("no access action anywhere in the section"); section 8 is the design-partner application only. Net: a visitor told Trial is "Self-serve · available now" has nowhere to click. That contradicts the Compact's own load-bearing logic — "hosted paths priced and truthfully accessible before the selective offer"; refusal is costly only if the alternative is reachable. No conversation owned the Compact page holistically at screen level, so this fell between id-proof and plans. Placement of the hosted access affordance is design, so it goes into the Branch A follow-up pass; the architect-safe part is amending the connective now, which currently overclaims that sections 1 and 2 are "already specced to screen level" for Branch A.

3. Note: the facts Runtime exclusion says "Always-on, trial duration, and agent counts live exclusively in section 3," and checklist item 1 re-verifies that exclusivity, while section 1's FINAL identification sentence contains "always-on." Read as plan entitlements it's consistent, but a literal build-time checker would false-flag section 1. Optional one-word clarification in the exclusion ("Plan-level always-on entitlements…"); note severity either way.

On the asymmetry, we agree it's defensible in both directions, and your condition is already met textually: checklist section C is titled "gates the consent claim" and item 15 says "must state." No fix needed. I'd add one note for the record: in Branch A the successor page is likely the Card, so on-page AdministratorAccess disclosure disappears at disposition if the role hasn't narrowed by then; that's coherent because the consent gate travels with onboarding, not the page, but it's worth a sentence in Part 4 only if you think a future reader would stumble. On credits comprehension: agree, note only — any aid beyond relative quantities needs shadow-period evidence that doesn't exist yet, so criterion 2 excuses it today.

So my severity ledger against yours: blocking — cost-line collision (conversation: plans/, close's text as input) and credential-figure existence (fact-check now plus one connective clause, not a design round). Should-fix — action-location sentence, abbreviation, Branch A scope including section 4's no-blanks correction, Q6/section-6 alignment, Branch A hosted-access gap recorded into the follow-up pass, kill-ledger identity-assets line, plus the close/ round on identity anchors. Notes — your two duplication items, my always-on exclusivity item, asymmetry successor wrinkle, credits comprehension. Where do you land on verification-first for the credential figure, and do you accept the downgrade of the two duplication items?

## Codex — turn 3 (2026-08-11 00:30)

The production check settles the credential dispute—and makes the defect worse.

1. I ran a privacy-safe aggregate query against production: there are **zero non-platform shared-credential rows, configured or otherwise**. The credential figure’s source state does not exist today.

   I accept verification-first, but reject a connective-only remedy. Calling Branch B “conditionally buildable” would make the document truthful while still failing criterion 2. The facts spec says no qualifying capture means the section fails build review, with no substitute. Therefore:

   1. Return to `plain-product-card/facts/` with the verified zero-row fact.
   2. The existing rejected alternatives remain constraints; the round may not casually revive a manufactured row, platform crop, or empty state.
   3. If it cannot produce an honest, presently available resolution, reopen judging because Branch B’s feasibility premise has failed.

   Waiting for organic future use is not “producible today.” The connective must ultimately record the result, but cannot repair the acceptance failure by itself.

2. I accept downgrading both duplication findings to notes.

   1. The deployment boundary is intentionally aggregate comprehension in Product facts and per-plan comparison in the grid; the facts closing correction knowingly preserves both.
   2. Hermes provenance and footer attribution divide product identification from license/source/non-affiliation hygiene. The minimal repeated referent is justified.

   I would record these as deliberate exceptions to the fact-once discipline, not defects.

3. I accept the Q6 finding as should-fix. It is a genuine authorization hole. Replace Q6’s quotation with section 6’s complete statement verbatim, including:

   1. Existing-partner support and exit terms.
   2. “Either.”
   3. The successor offer’s own economics.
   4. “This compact does not renew.”

   This is mechanical canonical reconciliation, not new design.

4. I accept the Branch A hosted-access gap. The combined Compact has declarative hosted availability but no reachable hosted action: id-proof removes `Sign up`, plans bans actions, and the application serves customer-cloud selection only. That undercuts the costly-refusal mechanism. Placement requires the Branch A screen pass. The connective must stop saying sections 1 and 2 are already complete for Branch A; their individual specs exist, but their integration is unresolved.

5. The remaining agreed fixes stand:

   1. Replace the false section-5 action-location sentence with:  
      “The hosted signup action lives in section 1 and does not repeat here.”
   2. Introduce the abbreviation in the identification sentence as `Humanity Rules (HumR)`.
   3. Expand both Branch A connective passages: section 4 can be specified before the answers, while gated sections await instantiated answers; sections 7 and 9 must be explicitly marked reused or adapted.
   4. Return identity anchors to `close/` and include the Compact signer in the follow-up scope.
   5. If anchors are not adopted, replace “its identity assets survive” in Part 4 with “its name, face, and location assets survive.”
   6. Clarify “Plan-level always-on entitlements” in the facts exclusion/checklist as a note-level cleanup.
   7. Change “Team card” to “Team column” wherever the close spec refers to the plans grid.

6. AdministratorAccess remains a defensible asymmetry, not a defect. Branch A needs it to make its exclusion and obligation machinery intelligible; Branch B may defer scope to the mandatory pre-authorization consent surface because it makes no limiting claim.

My revised ledger now matches yours except on the failed credential gate’s remedy: it is blocking, and production has confirmed the failure. I do not think the final audit can pass until `facts/` resolves it—or, failing that, judging reopens the Branch B verdict.

## Claude — turn 4 (2026-08-11 00:39)

Confirmed independently, and it's even starker than your query suggested: I ran the aggregate against production myself just now — **zero `IntegrationSharedCredential` rows exist anywhere**, platform org included, across all 12 organizations. The org-sharing feature shipped on 2026-07-18 and has never been used at org scope in production. So even the killed "platform crop" never had a populated table behind it. The F&F deployments evidently run on user-level credentials only. I withdraw the connective-only remedy; you're right that "conditionally buildable" is a truthful sentence describing a criterion-2 failure, not a repair. Your three-step remedy stands, with one sharpening I'll fold into the report: the `facts/` round's genuinely open question is whether section 2 can be truthful and complete without the figure — §3.7's no-figureless-section rule was written on the assumption a qualifying capture plausibly existed, so revisiting it under a verified structural absence is new-evidence redesign, not a kill revival; the three substitute assets stay dead.

That resolves the last dispute. Everything else in your revised ledger matches mine. The conversation has run its course; here is the standalone report.

---

# Final acceptance audit report — `duet/landing/spec.md`

**Verdict: DOES NOT PASS.** Two blocking defects, seven should-fix defects, six recorded notes. Fidelity is clean. Audit run 2026-08-11 by both agents; every load-bearing claim below was verified against the canonical transcripts, and the decisive feasibility fact was verified twice independently against production.

## Fidelity result — clean

All six extracts (verdict, Compact, Card, id-proof, plans, close) match their canonical boundaries byte-for-byte with no truncation (Codex, full comparison). The known hotspot — the facts spec's three closing corrections, applied by hand — was independently re-verified (Claude) against `facts/conversation.md` turn 5: the `desired_count=1` runtime anchor, the checklist-1 `customer_cloud` clause, and the §3.7/checklist-5 no-substitute failure mode all landed, in both required locations. No fidelity defects in the extracts. One connective-drift defect (D8 below).

## Blocking defects — require conversations before acceptance

**D1 — CRITERIA (criterion 2: producible today) + COHERENCE. The credential figure's source state is verified absent.** The facts spec (§3.1) requires capture from a populated, non-platform organization at `/integrations/org/provider-keys/`, forbids creating or reshaping rows, and (§3.7) permits no substitute — section 2 fails build review without the figure. Production state, verified 2026-08-11 by two independent read-only queries: zero shared-credential rows in any organization (platform included), 12 organizations total. Branch B is therefore not buildable as specced, and the verdict's Branch B premises ("no unresolved evidentiary experiment"; "no rare historical evidence") fail for section 2. Waiting for organic future use is not "producible today."
*Fix — not architect-applicable.* Return to `plain-product-card/facts/` carrying the verified zero-row fact. Constraints: the empty state, platform-org crop, and manufactured row remain killed. The open design question: whether the section can be truthful and complete without the figure (the Credentials row copy itself remains true — it describes the shipped interface, anchored to templates; what is impossible is the evidence). If the round cannot produce an honest, presently available resolution, reopen judging on Branch B's feasibility premise. The connective's Branch B line ("fully specced to screen level and buildable now") is false until the round returns and must then be rewritten to record the outcome; no interim architect edit suffices.

**D2 — COHERENCE. Plans/close collision on the Team cost-boundary line.** Close's section 5 copy rules and checklist item 6 require section 3 to carry, beside the Team price: "Team is $29 per agent per month. AWS infrastructure and model usage are separate. You use Bedrock in your AWS account or another provider you configure." The plans spec permits only its five-row grid, bans all additional prose in Branch B ("Nothing else"), and contains no separate-costs statement. The line cannot be treated as implicit: close's checklist verifies its presence *as a line*; the grid never says the $29 excludes infrastructure and model spend (the load-bearing comparison is against Operator's all-in $39); and the Card's parent text requires the transaction "truthful at the moment it is presented, not corrected later."
*Fix — not architect-applicable* (placement is screen design). Return to `plain-product-card/plans/` with close's sentence as binding input; the round may alternatively conclude the honest home is section 5's Team block, which amends close instead. The architect must not choose the placement.

## Should-fix defects — exact fixes, architect-applied except D9

**D3 — COHERENCE. False action-location sentence in close section 5.** Section 3 contains no actions ("No buttons, links… The page's single access action lives outside this section"); the Card's access action is section 1's `Sign up`. Replace exactly:
> "Hosted-plan actions live in section 3 and do not repeat here." → "The hosted signup action lives in section 1 and does not repeat here."

**D4 — COHERENCE. Q6's quotation does not match section 6's published statement.** Four deltas: Q6 omits "Existing partners keep their promised support and exit terms for the published program duration."; omits "either"; omits "with its own economics"; omits "This compact does not renew." The questionnaire's own rule requires each item to quote "the exact statement that would appear on the page," and only section 4 receives blanket authorization — so as written, Victor could sign all six without ever authorizing non-renewal, a clause the verdict's lifecycle ruling treats as binding. *Fix:* replace Q6's quoted statement with section 6's statement verbatim. Mechanical canonical reconciliation, no new design.

**D5 — COHERENCE. "HumR" used before it is introduced.** First page use is section 2's Deployment row; nothing establishes the abbreviation (the close transcript's claim that "the page has already established the HumR abbreviation" is false of the assembled page). *Fix:* the identification sentence becomes:
> "Humanity Rules (HumR) deploys always-on AI agents as separate services, each with saved work and access to approved tools."

Applies to both branches through the shared proof unit. Recorded caveat: this touches `id-proof/`'s tested FINAL copy; no alternative placement exists, so architect application is accepted.

**D6 — ACTIONABILITY. Branch A follow-up scope understated, in both connective locations.** Section 4 is Compact-specific with no screen spec but also no blanks (the questionnaire's blanket clause authorizes its copy verbatim; only layout is missing), so "becomes possible only once your answers exist" is wrong for it. Sections 7 and 9 are unaccounted for. *Fix, decision-procedure step 3:* replace the clause after the parenthetical with: "its Compact-specific sections (3, 4, 5, 6, 8) need a follow-up screen-level pass — sections 3, 5, 6, and 8 become writable only once your answers exist; section 4 is fully worded and needs layout only. Section 7 adapts Part 3's signature spec (the stage facts fold into the signer block), and section 9 reuses Part 3's footer spec." *Fix, Part 2 preamble:* the matching expansion of "sections 3, 5, 6, and 8 await your blanks and then need one further screen-level pass."

**D7 — COHERENCE. The assembled Compact page has no hosted access affordance.** The Compact write-up reserves "[current access action, verified at build time]" slots for Trial and Operator; the reused plans grid renders declarative Access-today cells and bans links, buttons, and visitor-addressed verbs; the id-proof Compact variant removes `Sign up` entirely; section 8 serves customer-cloud selection only. A visitor told Trial is "Self-serve · available now" has nowhere to act — contradicting the Compact's own mechanism ("hosted paths priced and truthfully accessible before the selective offer"; refusal is costly only when the alternative is reachable). No conversation owned the integrated Compact page; this fell between two parallel specs. *Fix:* placement is design — assigned to the Branch A follow-up pass. Architect-applied now: the connective amendments in D6 must also stop claiming sections 1 and 2 are complete for Branch A; their individual specs exist, their integration is unresolved.

**D8 — FIDELITY (connective drift). Part 4 kill ledger overstates what survived Meet the Builder.** Its canonical identity assets include "GitHub and LinkedIn anchors (exist today)"; the anchors did not survive into either branch. *Fix:* if the D9 round adopts anchors, the line becomes true and stands; otherwise replace "its identity assets survive as modules in both branches" with "its name, face, and location assets survive as modules in both branches."

**D9 — CRITERIA (criterion 7: alternatives beaten). Independent founder verification was never explored for the winners.** The brief's central question is whether this is a legitimate operation or an invented persona; both winning signature blocks carry only self-asserted name, portrait, and location. Meet the Builder established GitHub and LinkedIn as producible independent anchors; judging killed its continuity apparatus, never its anchors; `close/` never considered outbound identity links (its exclusions ban narrative, not links). *Fix — not architect-applicable:* focused return to `plain-product-card/close/` (section 4), outcome flowing into the Compact's section 7 through the Branch A pass. No silent insertion.

## Notes — recorded, no defect

**N1 — COHERENCE.** The HumR-cloud/customer-AWS boundary in both the facts Deployment row and the grid's Runs-in row is a deliberate exception to each-fact-once: aggregate comprehension prose versus per-plan comparison data, knowingly preserved by the facts round's closing correction 2. Record as exception.
**N2 — COHERENCE.** Facts Harness row plus footer attribution is a deliberate division — product identification versus license/source/non-affiliation hygiene — prescribed by the parent write-up itself; the repeated referent ("built on… Hermes Agent", "open-source") is the minimum that keeps the footer self-contained. Record as exception.
**N3 — COHERENCE.** The facts Runtime exclusion "Always-on… live exclusively in section 3" reads consistently as plan entitlements but could false-flag section 1's "always-on" at build. Optional one-word cleanup: "Plan-level always-on entitlements, trial duration, and agent counts live exclusively in section 3."
**N4 — COHERENCE.** Close refers to "Section 3's Team card"; plans rejected cards. Architect-applied cleanup: "Team column," wherever close references the grid.
**N5 — COHERENCE.** The AdministratorAccess asymmetry is defensible in both directions: Branch A needs on-page disclosure to make its exclusions and obligations intelligible; Branch B makes no narrowing claim (narrowing vocabulary is banned) and gates consent at the pre-authorization surface. This holds only while checklist item C.15 stays mandatory; its "must state" language already is. Successor wrinkle: in Branch A the on-page disclosure lapses when the successor Card ships — coherent, because the consent gate travels with onboarding, not the page.
**N6 — ACTIONABILITY.** Nothing tells a visitor what 500 or 2,000 credits accomplishes; the one-cent conversion was rejected and no other comprehension aid was tested. Any honest aid needs shadow-period usage evidence that does not yet exist (criterion 2), so this is deferred, tied to the billing design §11 shadow-period question already flagged in the plans spec.

## Criteria and actionability sweep

Outside the defects above, the seven criteria and the pricing constraint hold for both branch pages as specced: pricing appears in both branches with equal-prominence discipline; no section fails the trust-or-comprehension test; copy register rules enforce the never-AI-generated constraint structurally; kills are recorded throughout. Actionability: the one-decision read stands once D4 and D6 land and D1's status is recorded — the questionnaire is otherwise complete and self-contained, and everything Victor must produce is consolidated in Part 4 with per-spec checklists.

## Sequencing for the architect

Apply D3, D4, D5, D6/D7 connective amendments, D8 (conditional wording), N3, N4 now. Open three conversations: `plain-product-card/facts/` (D1, carrying the verified zero-row fact), `plain-product-card/plans/` (D2, carrying close's sentence), `plain-product-card/close/` (D9). The Branch A follow-up pass additionally owns D7's affordance placement and D9's Compact-side outcome. If the facts round cannot resolve D1 honestly, reopen judging. The spec cannot be stamped accepted until the D1 and D2 rounds return.

[END]

## Codex — conclusions (2026-08-11 00:40)

[spec.md](/Users/vmendi/Documents/projects/humanityrules/duet/landing/spec.md) does not pass final acceptance.

1. Fidelity is clean. All canonical extracts match without truncation, and the facts screen’s three hand-applied corrections landed correctly.

2. Two defects block acceptance:

   1. The required credential figure is impossible today: production contains zero organization-shared credential rows across all 12 organizations. Return to `plain-product-card/facts/`; if section 2 cannot remain complete without the figure, reopen judging.
   2. The close spec requires an explicit Team AWS/model-cost sentence that the plans grid omits and structurally forbids. Return to `plain-product-card/plans/` to decide its location.

3. Required exact corrections include:

   1. Point hosted signup to section 1, not section 3.
   2. Replace Q6’s incomplete quotation with section 6’s full statement verbatim.
   3. Introduce the abbreviation as “Humanity Rules (HumR)” in the identification sentence.
   4. Correct the Branch A follow-up scope, including section 4 and adaptations of sections 7 and 9.
   5. Stop claiming Branch A sections 1–2 are fully integrated: the Compact currently provides no hosted-plan access affordance.
   6. Correct the Meet the Builder identity-assets claim unless independent anchors are adopted.
   7. Replace “Team card” with “Team column.”

4. A focused `plain-product-card/close/` round must consider GitHub and LinkedIn identity anchors. They exist today and directly address whether Victor is independently verifiable rather than merely self-asserted.

5. AdministratorAccess disclosure remains a defensible branch asymmetry because Branch B’s pre-authorization consent gate is mandatory. Credit comprehension and two intentional cross-section fact repetitions remain notes, not defects.

6. No disagreement remains between the auditors. No repository files were changed. The required next sequence is: `facts/`, `plans/`, `close/`, then the Branch A screen pass; reopen judging if `facts/` cannot restore Branch B’s present-day feasibility.
