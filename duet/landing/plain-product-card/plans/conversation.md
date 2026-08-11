# plans

Claude: fable (xhigh) · Codex: gpt-5.6-sol (xhigh) · first: Codex

## Codex — turn 1 (2026-08-10 21:33)

My starting position: this should be one comparison table, not four independent pricing cards. Cards encourage oversized prices, unequal feature lists, and a “winner.” A shared row structure makes every commercial boundary unavoidable.

1. The verified factual floor

`docs/billing_design.md` and `humanityrules_app/services/billing/plans.py` currently agree:

- Trial: free; 500 one-time credits; seven days of runtime; one agent; HumR-hosted.
- Operator: $39/month; 2,000 credits/month; always-on; one agent; HumR-hosted.
- Team: $29/agent/month; unlimited always-on agents; customer AWS; bring your own model.
- Enterprise: custom; customer AWS; bring your own model; everything in Team plus SSO, compliance, and support.

I would not import SCIM, connectors, custom domains, “private VPC,” or “dedicated support” from the current billing template. Those claims exceed the billing design’s published plan definition or add detail the section does not need.

The repo now contains signup, trial-credit, billing-page, and Operator Checkout code, despite the billing document still saying “pre-implementation.” That proves implementation exists; it does not prove any plan is publicly obtainable in production. Access remains a build-time fact.

2. The structure I think wins

Wide screen: a single comparison grid.

- Header row: Trial, Operator, Team, Enterprise, left to right.
- A narrow label column precedes the four equal plan columns.
- Fact rows, top to bottom: Price; Runs in; Models; Includes; Access today.
- Each row spans all four plans before the next row begins.
- The longest cell sets the row depth across the table, preserving horizontal comparison.
- Plan names are the strongest text inside the table. Every fact value—including price—uses the same typographic rank and weight.
- Price earns attention by appearing first, not by becoming display typography.
- Access is a normal full row, never a badge, footnote, button, or disclaimer.
- No tinted plan, oversized number, checkmark feature list, ribbon, recommendation, or per-plan adjective.

On a narrow screen, the same data transposes into four consecutive plan records. Each repeats the five row labels in the same order. No tabs, carousel, or collapsed details: all four remain in one uninterrupted section.

That is how “equal prominence with price” becomes structural rather than aspirational.

3. First copy pass

Section heading: **Plans** — FINAL.

Row labels: **Price / Runs in / Models / Includes / Access today** — FINAL.

Plan values:

1. Trial

   - Price: **Free** — FINAL
   - Runs in: **HumR’s cloud** — FINAL
   - Models: **HumR-brokered models · 500 one-time credits** — provisional
   - Includes: **One agent · seven days of runtime** — FINAL
   - Access today: build-time value

2. Operator

   - Price: **$39/month** — FINAL
   - Runs in: **HumR’s cloud** — FINAL
   - Models: **HumR-brokered models · 2,000 credits/month** — provisional
   - Includes: **One always-on agent** — FINAL
   - Access today: build-time value

3. Team

   - Price: **$29/agent/month** — FINAL
   - Runs in: **Your AWS account** — FINAL
   - Models: **Bring your own: Bedrock or any provider you configure** — FINAL
   - Includes: **Unlimited always-on agents** — FINAL
   - Access today: build-time value

4. Enterprise

   - Price: **Custom** — FINAL
   - Runs in: **Your AWS account** — FINAL
   - Models: **Bring your own: Bedrock or any provider you configure** — FINAL
   - Includes: **Everything in Team, plus SSO, compliance, and support** — FINAL
   - Access today: build-time value

The two hosted model lines are provisional because “HumR-brokered models” is exact but internal-sounding. “Models included” is plainer, but slightly less precise. I currently prefer precision.

4. Access vocabulary

The four values must be mutually exclusive:

- **Purchasable now:** a visitor can complete the live self-service path and receive the plan without Victor acting.
- **Manually provisioned:** the plan is presently available on its published terms without qualification or negotiation, but Victor or an administrator must activate it.
- **Request access:** the visitor can submit a real request; approval or invitation follows, but no individualized sales process is implied.
- **Sales-led:** entry begins with a commercial conversation and depends on qualification, deal terms, or coordinated provisioning.

Choose exactly one per plan by walking the production path with a new, non-founder identity. A route or configured button is insufficient; the test ends only when the promised access exists. If none fits, publication is blocked rather than softened with “coming soon.”

One semantic objection: “Free / Purchasable now” is awkward if Trial is genuinely self-service. The mandated vocabulary treats “purchasable” as “obtainable without a human,” including a $0 transaction. I can live with that controlled meaning, but I do not think it is naturally good public copy.

5. Branch A

The grid does not change.

- Trial and Operator retain their independently verified access values.
- Team’s access cell becomes: **Request access · through the design-partner program below.**
- Enterprise should also read **Request access**, while the required scope statement beneath the table establishes that it uses the same selection process.
- The Compact’s Q1 scope statement then appears immediately below, with no new visual treatment that competes with the table.

In Branch B, that statement and Team’s program line disappear. The table itself then carries the trust argument. In Branch A, the identical table is the commercial map that bounds the compact.

My main challenge back to you: do you accept the shared grid over four cards, and do you prefer the precise “HumR-brokered models” or the plainer “Models included” for the hosted tiers?

## Claude — turn 2 (2026-08-10 21:37)

I re-verified independently before responding: `plans.py` and `billing_design.md` §2 agree with your floor exactly ($0/500-one-time/7-days/1-agent; $39/2,000-monthly/always-on/1-agent; $29-per-agent/unlimited/customer-AWS/BYO; custom/everything-in-Team-plus). Two repo details you didn't surface that matter: the billing doc explicitly flags the 2,000-credit Operator grant as a shadow-period question (§11 step 4 — "re-check grant/trial credit numbers against the observed burn distribution"), so that number is the likeliest to move between now and build; and §1 states flatly "Team and Enterprise are sales-led with no self-serve path," which is the strong prior your walk test will confirm or refute. Both belong on the checklist.

Now the answers and the pushback.

**1. Grid accepted — with one structural rule you didn't state.** Your argument for the shared grid over cards is right and I won't manufacture disagreement: cards create per-plan containers, containers invite hierarchy, and the parent's "no anchoring theater" is only enforceable when every plan lives inside one row structure. But the grid needs a rule that actually does the anti-CTA work: **access cells are declarative states, never instructions or links.** The parent's restraint discipline is one access action up top, one next action at the end. If the Access row reads "email Victor" / "sign up here" four times, the table becomes four competing CTAs and the discipline is dead. This constrains the display vocabulary below.

**2. Neither of your Models options.** "HumR-brokered models" fails not because it's internal-sounding but because "brokered" is unintroduced vocabulary at this point on the page — section 2 establishes credential brokering, not model brokering, and a visitor cannot be asked to transfer the concept. "Models included" fails differently: under the row label **Models**, the cell "Models included" says the word twice. The label already types the cell. So:

- Trial: **Included · 500 credits, one-time**
- Operator: **Included · 2,000 credits per month**
- Team / Enterprise: **Your own — Amazon Bedrock or any provider you configure**

"Included / Your own" is a minimal pair, and it answers the visitor's actual question — do I need my own API key? — which neither of your candidates did. Note "Amazon Bedrock," not "Bedrock"; the repo's shorthand is not the product's public name. Which models are included stays unstated: naming the lineup imports the rate card, which the billing doc marks provisional. I considered and reject a credit-definition line under the table ("1 credit = one cent of model use") — the billing design's own rule is that credits are never displayed as USD, and a token-equivalence line imports the rate card again. The relative quantities (500 once vs 2,000 monthly) carry the legible meaning, and the free trial makes the unit experiential. Record that as a rejection, per the brief.

**3. "HumR's cloud" should not survive to FINAL.** Same minimal-pair logic, applied to your Runs-in row: **Our AWS account / Your AWS account**. It is more concrete than "cloud" (it is literally an AWS account), it makes the row a single axis with two values, and it sidesteps the question of whether "HumR" — an internal short name for a page branded Humanity Rules — has even been introduced by section 3.

**4. Two copy corrections.** Enterprise's Includes should be **"Everything in Team, plus SSO, compliance, and support terms"** — the Compact's canonical section 2 already lands on "terms," and it matters: "support" as a bare noun claims a shipped feature; "support terms" truthfully types it as negotiated deal content, which is what a custom plan actually is. And Trial's "seven days of runtime" is ambiguous between seven calendar days and seven accumulated days; the repo means the former (suspension at `trial_runtime_days` after first deployment). Say **"One agent · runs for seven days."** Do not add "then it pauses; upgrading wakes it" even though it's the billing design's best trust fact — trial suspension automation is explicitly out of MVP scope and trials are hand-created today, so that sentence describes decided design, not verified behavior. It goes on the build-time checklist, not in the copy.

**5. Access display vocabulary.** Your four definitions and the walk test I accept wholesale, including "publication blocked rather than softened." But your semantic objection to "Free / Purchasable now" dissolves once you separate the *selection* taxonomy (the parent's four states, fixed) from the *display* rendering (this section's deliverable). Display values, one per state, all declarative:

- purchasable now → **Self-serve — available now**
- manually provisioned → **Available now — accounts set up by hand**
- request access → **By request**
- sales-led → **Sales-led**

"Purchasable" never reaches the screen, so the $0 awkwardness never exists. "Set up by hand" is the honest datasheet register for the manual state and I'd defend it against a softer synonym. The walk test needs one sharpening: for Operator, "purchasable now" is structurally impossible until the LLC files and Stripe live mode exists — and per current prod state the billing migrations aren't even deployed — so the checkout code you correctly discounted is doubly insufficient evidence.

**6. Branch A: your Enterprise treatment re-opens the backdoor the Compact killed.** You give Team "Request access · through the design-partner program below" and Enterprise bare "Request access," letting the scope statement do the unification. That asymmetry is exactly the two-lane reading the Compact's development fought over — Enterprise as a differently-labeled entry is where "a larger check routes around the program" lives. Both customer-cloud cells must carry the **identical** value: **Through the design-partner program below.** The "Request access ·" prefix is redundant with it. One value, twice, then the Q1 scope statement beneath. Identical cells are also the structural proof of Q1, stronger than the statement alone.

**7. Branch B: the statement doesn't "disappear," it moves — and you missed the verdict's amendment.** The judging verdict attaches a Branch-B amendment to *section 5*, not section 3: state that no external organization currently runs HumR in its own AWS account, and describe exactly what proceeding with Team entails today, verified as present practice. So in Branch B the table's Team/Enterprise access cells carry their walk-test values (prior: Sales-led), and the no-external-org disclosure lands in section 5. The screen spec must record this coordination, or a builder will either duplicate the fact (violating appears-once) or drop it entirely.

**Checklist additions** beyond yours: prod deployment state of the billing migrations and billing page before any hosted access value above "by request"; Stripe live mode ⇐ LLC filing gating Operator's "self-serve"; a fresh-identity walk of trial signup specifically (the route existing is not the gate being open); re-verify all five numbers against `plans.py` at build, with the 2,000 grant flagged as the shadow-period variable; verify actual day-7 trial behavior before any pause/wake copy; Branch A only — Q1 affirmed verbatim before the scope statement ships.

Open for your next turn: do you accept "Our AWS account / Your AWS account," the "Included / Your own" Models pair, identical Branch-A customer-cloud cells, and the display-vocabulary split? If so, I think the remaining work is the standalone screen spec, which I'm prepared to draft.

## Codex — turn 3 (2026-08-10 21:38)

I accept the shared-grid rule and all four decisions, with one copy correction.

1. **Runs in:** accept **Our AWS account / Your AWS account** — FINAL. The minimal pair is clearer than “HumR’s cloud,” and section 1 has already established who “our” refers to.

2. **Models:** accept:

   - Trial: **Included · 500 credits, one-time** — FINAL against the current registry.
   - Operator: **Included · 2,000 credits per month** — FINAL against the current registry, explicitly reopened at build time because §11 names the grant as provisional.
   - Team and Enterprise: **Your own — Amazon Bedrock or any provider you configure** — FINAL.

   Also record the rejected credit-definition line. It imports provisional rate-card logic and falsely invites monetary conversion even though the product deliberately does not display credits as USD.

3. **Access:** accept the selection/display split and the rule that cells are declarative only:

   - `purchasable now` → **Self-serve — available now**
   - `manually provisioned` → **Available now — accounts set up by hand**
   - `request access` → **By request**
   - `sales-led` → **Sales-led**

   No links, verbs addressed to the visitor, buttons, or contact instructions appear in the grid.

4. **Branch A:** accept identical customer-cloud cells:

   - Team: **Through the design-partner program below.**
   - Enterprise: **Through the design-partner program below.**

   That repetition is necessary data symmetry, not copy padding. The Q1 statement beneath supplies the selection boundary; no larger-contract lane is implied.

5. **Branch B coordination:** accepted. Section 3 owns each plan’s classified access state. Section 5 owns the separate facts that there are no external customer-cloud organizations and what proceeding with Team operationally entails. Section 5 should describe the sequence without repeating the label “Sales-led.”

My sole rejection is **“One agent · runs for seven days.”** It still leaves the clock origin unstated and may sound like a continuous-uptime claim. The repo’s decided term starts at first deployment. Use:

**One agent · seven days from first deployment** — FINAL against the current design, contingent on the build-time fulfillment check.

That is shorter and more exact. It also avoids promising pause/wake behavior that is designed but deferred.

Your checklist additions all stand. I would organize the final checklist into three gates:

1. **Plan-definition gate:** compare every displayed value with both `plans.py` and `billing_design.md`; reopen any disagreement; specifically re-evaluate the 2,000-credit Operator grant after shadow-period evidence.

2. **Access gate:** verify deployed migrations and billing UI, walk Trial with a fresh identity, classify every plan through its real production stopping point, and test Operator end to end only after LLC filing permits Stripe live mode—including the live price, Checkout, webhook, plan transition, and credit grant.

3. **Branch gate:** verify actual Team/Enterprise handling; verify day-seven Trial fulfillment without adding unimplemented pause/wake copy; for Branch A require Q1’s verbatim affirmation and identical program cells; for Branch B reserve the zero-external-deployment and current Team-process facts for section 5.

I think you can now draft the standalone screen spec. The only thing I’ll scrutinize is whether it remains implementation-complete without drifting into styling instructions or repeating facts in explanatory prose.

## Claude — turn 4 (2026-08-10 21:40)

Accepted: **One agent · seven days from first deployment** is better than mine — it names the clock origin, which "runs for" left implicit. One correction of our shared copy before I draft, and it's not mine to waive: Victor's standing landing-copy rule is **no em dashes**, and three of our agreed strings use them. I've re-punctuated with the mid-dot we already use as the intra-cell separator, and a colon where the dash was appositive. Wording unchanged. With that, everything is FINAL except what is structurally build-time.

The standalone spec follows.

---

# Screen spec — Plain Product Card, Section 3: Plans and commercial boundaries

## Job and position

Third section of the Plain Product Card, between the product-facts section and Victor's signature block. Its job, per the parent write-up: the page's center of gravity; the transaction presented so exactly that the pricing table itself is the trust argument. In Branch A (Design-Partner Compact), the identical structure serves the Compact's section 2, where its job changes: not itself the trust argument, but the map that bounds the compact.

## Element inventory

1. Section heading: **Plans** — FINAL. Nothing between the heading and the grid: no intro sentence, no subtitle.
2. One comparison grid: a narrow label column, then four plan columns — Trial, Operator, Team, Enterprise, left to right. Five fact rows, top to bottom: **Price / Runs in / Models / Includes / Access today** — FINAL.
3. Branch A only: the Q1 scope statement as plain body text beneath the grid (see Branch A below).
4. Nothing else. No buttons, links, tooltips, expanders, footnotes, badges, or icons anywhere in the section. The page's single access action lives outside this section.

## Layout and prominence rules

1. Each fact row spans all four plan columns before the next row begins; the longest cell sets the row's depth, so every plan is comparable along one horizontal line.
2. The four plan columns are equal in width and treatment. No column carries a tint, border weight, size, or ordering device the others lack.
3. Three text ranks in the section, total: plan names (strongest); all fact values, price included, at one uniform rank below; row labels quietest. Price gets attention by being the first row, not by typography. This is how equal-prominence-with-price is achieved structurally: price is one row among five in a grid that gives no cell a way to outrank another.
4. Access today is a normal full row — never a badge, footnote, or annotation.
5. Cells are declarative states and facts only: no links, no buttons, no verbs addressed to the visitor, no contact instructions.
6. Two facts sharing a cell are separated by a spaced middle dot ( · ); a cell may break into two lines at that separator on narrow columns.
7. A value repeated across plan columns (e.g. "Your AWS account" twice) is data symmetry, not fact repetition; the each-fact-once rule governs prose, not grid values.
8. Narrow screens: the grid transposes into four consecutive plan records in the same order, each headed by the plan name at header rank, followed by the five label–value pairs in the same row order. No tabs, carousel, or collapsed details; all four records form one uninterrupted section.

## Copy — FINAL except Access today

| | Trial | Operator | Team | Enterprise |
|---|---|---|---|---|
| **Price** | Free | $39/month | $29/agent/month | Custom |
| **Runs in** | Our AWS account | Our AWS account | Your AWS account | Your AWS account |
| **Models** | Included · 500 credits, one-time | Included · 2,000 credits per month | Your own: Amazon Bedrock or any provider you configure | Your own: Amazon Bedrock or any provider you configure |
| **Includes** | One agent · seven days from first deployment | One always-on agent | Unlimited always-on agents | Everything in Team, plus SSO, compliance, and support terms |
| **Access today** | *build-time* | *build-time* | *build-time* | *build-time* |

All values verified against `humanityrules_app/services/billing/plans.py` and `docs/billing_design.md` §2 on 2026-08-10. Operator's 2,000-credit grant is FINAL against the current registry but explicitly reopened at build time: billing design §11 names it a shadow-period question.

## Access today: vocabulary and selection

Selection taxonomy (fixed by the parent write-up) and display rendering (this spec), one per state, mutually exclusive:

| State | Cell reads | Meaning |
|---|---|---|
| purchasable now | Self-serve · available now | A visitor completes the live self-service path and receives the plan with no human acting. |
| manually provisioned | Available now · accounts set up by hand | Available on published terms without qualification or negotiation, but Victor activates it. |
| request access | By request | A real request path exists; approval or invitation follows; no individualized sales process implied. |
| sales-led | Sales-led | Entry begins with a commercial conversation and depends on qualification, deal terms, or coordinated provisioning. |

Selection procedure: walk each plan's path on production with a fresh, non-founder identity. A route, page, or configured button is insufficient; the test ends only when the promised access exists. Classify at the real stopping point. If no state fits a plan, publication is blocked; no state may be softened and nothing is labeled "coming soon." Current priors, not conclusions: billing design §1 says Team and Enterprise are sales-led with no self-serve path; Operator cannot classify as self-serve before Stripe live mode exists.

## Branch A variant (table serves the Compact's section 2)

1. The grid, its copy, and its rules are unchanged.
2. Trial and Operator keep their independently verified access values.
3. Team and Enterprise Access today both read, identically: **Through the design-partner program below.** — FINAL. The repetition is data symmetry; two different values would imply a second entry lane for a larger contract, which the Compact's Q1 forbids.
4. Beneath the grid, the Q1 scope statement appears verbatim as plain body text, with no box, tint, or treatment competing with the grid. Its wording is owned by the Compact write-up and ships only after Victor affirms Q1 verbatim.

## Branch B coordination

Section 3 owns each plan's classified access state and nothing more. Section 5 owns, per the verdict's amendment: the fact that no external organization runs HumR in its own AWS account, and the description of what proceeding with Team operationally entails today, verified as present practice. Section 5 describes that sequence without repeating the label "Sales-led," so no fact appears twice.

## Build-time gates

**Gate 1 — plan definition.** Diff every displayed value against `plans.py` and `billing_design.md`; any disagreement reopens the cell. Re-evaluate the 2,000-credit Operator grant against shadow-period burn evidence. Confirm Bedrock capability still backs the "Your own" claim for Team and Enterprise, and Enterprise's terms list still reads SSO, compliance, support. Confirm the trial clock still starts at first deployment, and check what actually happens at day seven; if fulfillment contradicts the published term, reopen the copy. No pause/wake copy in any case: that behavior is decided design, deferred, unverified.

**Gate 2 — access classification.** Verify the billing migrations and billing page are deployed in production before any hosted plan classifies above "By request." Walk Trial signup with a fresh identity; the route existing is not the gate being open. Operator's "Self-serve · available now" requires, in order: LLC filed → Stripe live mode → live price, Checkout, webhook, plan transition, and credit grant verified end to end. Classify Team and Enterprise through their real production stopping points; the sales-led prior is confirmed or replaced, never assumed.

**Gate 3 — branch.** Branch A: Q1 affirmed verbatim before the scope statement ships; both customer-cloud cells identical. Branch B: the zero-external-deployment fact and the Team-process description are reserved for section 5 and verified there.

## Rejected on the way (so it is not reinvented)

1. Four pricing cards: per-plan containers invite hierarchy, oversized prices, and a winner; the shared grid makes every boundary a comparable row.
2. A credit-definition line beneath the grid ("1 credit = one cent"): imports the provisional rate card and invites the USD conversion the product deliberately never displays. Relative quantities carry the meaning; the free trial makes the unit experiential.
3. "HumR-brokered models": unintroduced vocabulary — section 2 establishes credential brokering, not model brokering. "Models included" as a cell: repeats its own row label. "HumR's cloud": less concrete than the Our/Your AWS account minimal pair.
4. Pause/wake trial copy: designed, deferred, unverifiable at present.
5. Access cells as instructions or links: turns the grid into four CTAs and breaks the page's one-action discipline.
6. A "Request access ·" prefix or any asymmetric Enterprise access cell in Branch A: reopens the larger-contract side door.
7. Ribbons, "most popular," tints, per-plan adjectives, anchoring devices: excluded by the parent write-up; restated here as binding on the grid.

---

Back to you for scrutiny. The two places I'd double-check if I were you: whether the spec's three-rank rule counts as structure or drifts into styling (I claim structure — it names relations, no sizes or faces), and whether "Available now · accounts set up by hand" survives your reading now that the em dash is gone.

## Codex — turn 5 (2026-08-10 21:42)

Close, but it is not yet standalone. I see six corrections.

1. The three-rank rule is structural, not excessive styling. Change “in the section, total” to **“inside the grid.”** The section heading uses the page-wide section-heading rank, so the current wording accidentally creates a fourth rank while claiming only three.

2. The grid still needs visible geometry. Add:

   **All grid text is left-aligned and top-aligned within its row. A horizontal rule separates the header and each fact row across the full grid. There are no vertical rules, individual cell boxes, rounded containers, or enclosing card. On narrow screens, one horizontal rule separates consecutive plan records.**

   Without this, a builder still has to invent whether the “grid” looks like a table, four cards, or free-floating columns.

3. The Branch A statement must be present verbatim. Referring the builder to another write-up breaks the standalone requirement. Include:

   **“Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria.”**

4. **“Available now · accounts set up by hand” does not survive my reading.** “Accounts” can mean user login accounts, and the plural is awkward inside one plan cell. Use:

   **Available now · set up by hand** — FINAL.

   The plan column supplies the referent. This keeps the plain-language honesty without introducing provisioning jargon.

5. Gate 2 incorrectly treats the access taxonomy as an ordering when it says “classifies above ‘By request,’” and it unnecessarily makes Trial depend on the billing page. Split the checks:

   1. Trial self-service requires a fresh production identity to complete signup, receive 500 credits, and reach a deployed first agent without human action.
   2. Operator self-service requires deployed billing migrations and billing page, filed LLC, Stripe live mode, a deployed live Stripe price of USD $39 recurring monthly, Checkout, signed webhook delivery, Operator transition, and the 2,000-credit grant.
   3. Manual availability requires a test organization to be activated at the published terms through the actual manual process.
   4. “By request” and “Sales-led” require their real inbound path and current handling process to exist, not merely Victor’s intention to handle them.

6. Gate 1 should verify both halves of two claims:

   1. Confirm the hosted plans still run in an AWS account controlled by Humanity Rules before retaining **Our AWS account**.
   2. Confirm both Amazon Bedrock access and support for customer-configured model providers before retaining **Amazon Bedrock or any provider you configure**. Checking only `bedrock_enabled` does not substantiate the second half.

With those changes, the section is implementation-complete, visually imaginable, and faithful to both verdict branches.

## Claude — turn 6 (2026-08-10 21:43)

All six corrections accepted. Two of them I'll sharpen while integrating, and both sharpenings favor your direction rather than resist it.

On your correction 3: including the Q1 statement verbatim exposes a mismatch neither of us flagged. The statement says each hosted plan "shows its truthful current access method **beside its price**," but our grid puts access four rows below price. I am not rewriting affirmed Q1 language from inside this spec; instead the spec records the reading that satisfies it — access appears in the same plan column (wide) or same plan record (narrow) — and notes that any wording change belongs to the Compact's Q1 process, not this section.

On your correction 6.2: the second half of "any provider you configure" is genuinely at risk, not just formally unverified — the current model-preset surface is narrower than "any provider." So the gate needs a failure consequence, not just a check: if the second half cannot be substantiated at build time, the cell shrinks to **Your own: Amazon Bedrock**. A claim that fails verification loses its clause; it doesn't get softened.

The final spec, complete and integrated:

---

# Screen spec — Plain Product Card, Section 3: Plans and commercial boundaries

## Job and position

Third section of the Plain Product Card, between the product-facts section and Victor's signature block. Its job, per the parent write-up: the page's center of gravity; the transaction presented so exactly that the pricing table itself is the trust argument. In Branch A (Design-Partner Compact), the identical structure serves the Compact's section 2, where its job changes: not itself the trust argument, but the map that bounds the compact.

## Element inventory

1. Section heading: **Plans** — FINAL, at the page-wide section-heading rank. Nothing between the heading and the grid: no intro sentence, no subtitle.
2. One comparison grid: a narrow label column, then four plan columns — Trial, Operator, Team, Enterprise, left to right. Five fact rows, top to bottom: **Price / Runs in / Models / Includes / Access today** — FINAL.
3. Branch A only: the Q1 scope statement as plain body text beneath the grid (see Branch A below).
4. Nothing else. No buttons, links, tooltips, expanders, footnotes, badges, or icons anywhere in the section. The page's single access action lives outside this section.

## Layout and prominence rules

1. Each fact row spans all four plan columns before the next row begins; the longest cell sets the row's depth, so every plan is comparable along one horizontal line.
2. The four plan columns are equal in width and treatment. No column carries a tint, border weight, size, or ordering device the others lack.
3. All grid text is left-aligned and top-aligned within its row. A horizontal rule separates the header row and each fact row across the full grid. There are no vertical rules, individual cell boxes, rounded containers, or enclosing card.
4. Three text ranks inside the grid: plan names (strongest); all fact values, price included, at one uniform rank below; row labels quietest. Price gets attention by being the first row, not by typography. This is how equal-prominence-with-price is achieved structurally: price is one row among five in a grid that gives no cell a way to outrank another.
5. Access today is a normal full row — never a badge, footnote, or annotation.
6. Cells are declarative states and facts only: no links, no buttons, no verbs addressed to the visitor, no contact instructions.
7. Two facts sharing a cell are separated by a spaced middle dot ( · ); a cell may break into two lines at that separator on narrow columns.
8. A value repeated across plan columns (e.g. "Your AWS account" twice) is data symmetry, not fact repetition; the each-fact-once rule governs prose, not grid values.
9. Narrow screens: the grid transposes into four consecutive plan records in the same order, each headed by the plan name at header rank, followed by the five label–value pairs in the same row order. One horizontal rule separates consecutive plan records. No tabs, carousel, or collapsed details; all four records form one uninterrupted section.

## Copy — FINAL except Access today

| | Trial | Operator | Team | Enterprise |
|---|---|---|---|---|
| **Price** | Free | $39/month | $29/agent/month | Custom |
| **Runs in** | Our AWS account | Our AWS account | Your AWS account | Your AWS account |
| **Models** | Included · 500 credits, one-time | Included · 2,000 credits per month | Your own: Amazon Bedrock or any provider you configure | Your own: Amazon Bedrock or any provider you configure |
| **Includes** | One agent · seven days from first deployment | One always-on agent | Unlimited always-on agents | Everything in Team, plus SSO, compliance, and support terms |
| **Access today** | *build-time* | *build-time* | *build-time* | *build-time* |

All values verified against `humanityrules_app/services/billing/plans.py` and `docs/billing_design.md` §2 on 2026-08-10. Operator's 2,000-credit grant is FINAL against the current registry but explicitly reopened at build time: billing design §11 names it a shadow-period question.

## Access today: vocabulary and selection

Selection taxonomy (fixed by the parent write-up) and display rendering (this spec), one per state. The states are categories, not an ordered scale; each is selected on its own evidence:

| State | Cell reads | Meaning |
|---|---|---|
| purchasable now | Self-serve · available now | A visitor completes the live self-service path and receives the plan with no human acting. |
| manually provisioned | Available now · set up by hand | Available on published terms without qualification or negotiation, but Victor activates it. |
| request access | By request | A real request path exists; approval or invitation follows; no individualized sales process implied. |
| sales-led | Sales-led | Entry begins with a commercial conversation and depends on qualification, deal terms, or coordinated provisioning. |

Selection procedure: walk each plan's path on production with a fresh, non-founder identity. A route, page, or configured button is insufficient; the test ends only when the promised access exists. Classify at the real stopping point. If no state fits a plan, publication is blocked; no state may be softened and nothing is labeled "coming soon." Current priors, not conclusions: billing design §1 says Team and Enterprise are sales-led with no self-serve path; Operator cannot classify as self-serve before Stripe live mode exists.

## Branch A variant (table serves the Compact's section 2)

1. The grid, its copy, and its rules are unchanged.
2. Trial and Operator keep their independently verified access values.
3. Team and Enterprise Access today both read, identically: **Through the design-partner program below.** — FINAL. The repetition is data symmetry; two different values would imply a second entry lane for a larger contract, which the Compact's Q1 forbids.
4. Beneath the grid, as plain body text with no box, tint, or treatment competing with the grid, the Q1 scope statement verbatim:

   > "Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria."

   This wording is owned by the Compact's Q1 and ships only after Victor affirms it verbatim. Its phrase "beside its price" is satisfied by the access value appearing in the same plan column (wide) or plan record (narrow); any change to the phrase itself goes through the Compact, not this spec.

## Branch B coordination

Section 3 owns each plan's classified access state and nothing more. Section 5 owns, per the verdict's amendment: the fact that no external organization runs HumR in its own AWS account, and the description of what proceeding with Team operationally entails today, verified as present practice. Section 5 describes that sequence without repeating the label "Sales-led," so no fact appears twice.

## Build-time gates

**Gate 1 — plan definition.**
1. Diff every displayed value against `plans.py` and `billing_design.md`; any disagreement reopens the cell. Re-evaluate the 2,000-credit Operator grant against shadow-period burn evidence.
2. Confirm the hosted plans still run in an AWS account controlled by Humanity Rules before retaining **Our AWS account**.
3. Confirm both halves of the Models claim for Team and Enterprise: working Amazon Bedrock access, and real support for customer-configured model providers beyond it. Checking `bedrock_enabled` substantiates only the first half. If the second half cannot be substantiated, the cell shrinks to **Your own: Amazon Bedrock** — a failed clause is removed, never softened.
4. Confirm Enterprise's terms list still reads SSO, compliance, support.
5. Confirm the trial clock still starts at first deployment, and check what actually happens at day seven; if fulfillment contradicts the published term, reopen the copy. No pause/wake copy in any case: that behavior is decided design, deferred, unverified.

**Gate 2 — access classification.** General rule: classification stops at the real stopping point; if no state fits a plan, publication is blocked. Per state:
1. Trial "Self-serve · available now" requires a fresh production identity to complete signup, receive the 500-credit grant, and reach a deployed first agent without human action. Trial's classification does not depend on the billing page.
2. Operator "Self-serve · available now" requires: deployed billing migrations and billing page; filed LLC; Stripe live mode (the live Stripe account depends on the legal entity); a deployed live Stripe price of USD $39 recurring monthly; Checkout; signed webhook delivery; the Operator plan transition; and the 2,000-credit grant — verified end to end.
3. "Available now · set up by hand" requires a test organization to be activated at the published terms through the actual manual process.
4. "By request" and "Sales-led" require their real inbound path and current handling process to exist, not merely Victor's intention to handle them.

**Gate 3 — branch.** Branch A: Q1 affirmed verbatim before the scope statement ships; both customer-cloud cells identical. Branch B: the zero-external-deployment fact and the Team-process description are reserved for section 5 and verified there.

## Rejected on the way (so it is not reinvented)

1. Four pricing cards: per-plan containers invite hierarchy, oversized prices, and a winner; the shared grid makes every boundary a comparable row.
2. A credit-definition line beneath the grid ("1 credit = one cent"): imports the provisional rate card and invites the USD conversion the product deliberately never displays. Relative quantities carry the meaning; the free trial makes the unit experiential.
3. "HumR-brokered models": unintroduced vocabulary — section 2 establishes credential brokering, not model brokering. "Models included" as a cell: repeats its own row label. "HumR's cloud": less concrete than the Our/Your AWS account minimal pair. "Accounts set up by hand": the plural reads as login accounts; the plan column already supplies the referent.
4. Pause/wake trial copy: designed, deferred, unverifiable at present.
5. Access cells as instructions or links: turns the grid into four CTAs and breaks the page's one-action discipline.
6. A "Request access ·" prefix or any asymmetric Enterprise access cell in Branch A: reopens the larger-contract side door.
7. Ribbons, "most popular," tints, per-plan adjectives, anchoring devices: excluded by the parent write-up; restated here as binding on the grid.

---

That is the section: one heading, one ruled five-row grid with every value at a single rank, four build-time access states with a closed vocabulary, one branch-dependent statement beneath it, and three gates that decide what may ship. I believe it now meets both bars — a builder implements it without questions, and the screen is imaginable with eyes closed.

[END]

## Codex — conclusions (2026-08-10 21:43)

The section is settled as a standalone, implementation-ready screen specification. No disagreements remain.

1. **Structure:** One heading, **Plans**, followed immediately by a shared comparison grid. Columns are Trial, Operator, Team, and Enterprise. Rows are Price, Runs in, Models, Includes, and Access today.

2. **Prominence:** All plan columns receive equal treatment. Price is the first row but uses the same text rank as every other value. The grid is left-aligned, separated by horizontal rules, and contains no cards, vertical rules, highlighted plans, badges, icons, or CTAs.

3. **Final plan copy:**

   1. Trial: Free; Our AWS account; Included · 500 credits, one-time; One agent · seven days from first deployment.
   2. Operator: $39/month; Our AWS account; Included · 2,000 credits per month; One always-on agent.
   3. Team: $29/agent/month; Your AWS account; Your own: Amazon Bedrock or any provider you configure; Unlimited always-on agents.
   4. Enterprise: Custom; Your AWS account; the same model terms as Team; Everything in Team, plus SSO, compliance, and support terms.

4. **Access vocabulary:** The internal states and public renderings are fixed:

   1. `purchasable now`: **Self-serve · available now**
   2. `manually provisioned`: **Available now · set up by hand**
   3. `request access`: **By request**
   4. `sales-led`: **Sales-led**

   Access cells are declarative text only. Each state must be selected through an end-to-end production test with a fresh, non-founder identity.

5. **Responsive layout:** On narrow screens, the grid becomes four consecutive plan records with the same five label-value pairs. All plans remain visible without tabs, carousels, or collapsed content.

6. **Branch A:** The grid remains structurally identical. Team and Enterprise both read **Through the design-partner program below.** The Compact’s Q1 scope statement appears verbatim beneath the grid only after Victor affirms it.

7. **Branch B:** Section 3 contains only the verified access classification. Section 5 separately owns the zero-external-customer-cloud disclosure and the actual Team onboarding sequence, without repeating “Sales-led.”

8. **Build-time gates:**

   1. Recheck every plan value against `plans.py` and `billing_design.md`.
   2. Reopen Operator’s 2,000-credit grant based on shadow-period burn evidence.
   3. Verify the hosted AWS boundary and customer-configured model-provider support. If broad provider support fails verification, Team and Enterprise shrink to **Your own: Amazon Bedrock**.
   4. Verify Trial signup, its 500-credit grant, first deployment, clock origin, and day-seven behavior.
   5. Operator self-service requires the LLC filing, Stripe live mode, a live USD $39 monthly price, deployed billing infrastructure, Checkout, signed webhook delivery, plan transition, and credit grant.
   6. Verify the real manual, request, and sales-led processes rather than inferring them from code or intent.
   7. Block publication if any plan fits none of the four access states.

9. **Rejected:** Four pricing cards, credit-to-USD explanatory copy, model-brokering jargon, pause-and-wake promises, access instructions inside the table, asymmetric Branch A customer-cloud paths, ribbons, highlighted plans, and pricing theater.

The artifact produced is the complete screen specification recorded in Claude’s final message. No repository files were changed.

## Victor (2026-08-11 00:42)

Acceptance audit finding, BLOCKING — defect D2 in ../../audit/conversation.md, read it first. The close spec's copy rules and its checklist item 6 require, beside the Team price, this line: 'Team is $29 per agent per month. AWS infrastructure and model usage are separate. You use Bedrock in your AWS account or another provider you configure.' Your grid bans all additional prose in Branch B ('Nothing else') and carries no separate-costs statement, and the omission is substantive: the visitor compares Operator's all-in $39 against Team's $29-plus-costs, and the transaction must be truthful at the moment it is presented, not corrected later. Decide the honest home of that fact as screen design: either amend your section 3 spec to carry it (and say exactly how without breaking your grid discipline), or conclude its honest home is close's section 5 Team block and specify the exact amendment to close's spec. The architect will not choose placement. Issue an amended standalone spec recording the decision.

## Codex — turn 7 (2026-08-11 00:44)

Decision: the separate-cost fact belongs in section 3, inside the Team Price cell. Section 5 is too late because this fact changes how the visitor interprets $29 against Operator’s $39. Enterprise receives the same disclosure because the same customer-cloud cost boundary applies. The grid remains five rows and gains no explanatory prose.

# Screen spec: Plain Product Card, Section 3

## Job and position

This is the Plain Product Card’s third section, between Product facts and Victor’s signature. The pricing comparison is the section’s trust argument.

In Branch A, the same structure becomes the Design-Partner Compact’s second section. There it maps the commercial surface and bounds the compact.

## Element inventory

1. Section heading: **Plans** (FINAL). It uses the page-wide section-heading rank.

2. One comparison grid with a narrow label column followed by four equal plan columns:

   1. Trial
   2. Operator
   3. Team
   4. Enterprise

3. Five fact rows, in this order:

   1. **Price**
   2. **Runs in**
   3. **Models**
   4. **Includes**
   5. **Access today**

4. Branch A only: the affirmed Q1 scope statement beneath the grid.

5. Nothing else. There is no introductory copy, subtitle, separate cost note, button, link, tooltip, badge, icon, footnote, or expander.

## Layout and prominence

1. Each row crosses all four plans before the next row begins. Its longest value determines the row depth.

2. All four plan columns have equal width and treatment. No plan receives a tint, heavier rule, larger type, recommendation, or distinct container.

3. All grid text is left-aligned and top-aligned.

4. A horizontal rule separates the header and every fact row. There are no vertical rules, individual cell boxes, rounded plan containers, or enclosing card.

5. Inside the grid there are three text ranks:

   1. Plan names
   2. All fact values, including prices and cost boundaries
   3. Row labels

6. Price receives attention by appearing first. It is not larger or heavier than the other fact values.

7. Access today is an ordinary fact row, never a badge, instruction, or link.

8. Two facts inside one cell use a spaced middle dot. The cell may break at that separator without demoting either fact.

9. On narrow screens, the grid becomes four consecutive plan records in the same plan order. Each record contains the same five label-value pairs. A horizontal rule separates records. Nothing is hidden behind tabs, carousels, or collapsed details.

## Plan copy

All wording is FINAL against the current plan definitions, except Access today and the explicitly named build-time clauses.

1. **Trial**

   1. Price: **Free**
   2. Runs in: **Our AWS account**
   3. Models: **Included · 500 credits, one-time**
   4. Includes: **One agent · seven days from first deployment**
   5. Access today: selected at build time

2. **Operator**

   1. Price: **$39/month**
   2. Runs in: **Our AWS account**
   3. Models: **Included · 2,000 credits per month**
   4. Includes: **One always-on agent**
   5. Access today: selected at build time

3. **Team**

   1. Price: **$29/agent/month · AWS infrastructure and model usage are separate.**
   2. Runs in: **Your AWS account**
   3. Models: **Your own: Amazon Bedrock or any provider you configure**
   4. Includes: **Unlimited always-on agents**
   5. Access today: selected at build time

4. **Enterprise**

   1. Price: **Custom · AWS infrastructure and model usage are separate.**
   2. Runs in: **Your AWS account**
   3. Models: **Your own: Amazon Bedrock or any provider you configure**
   4. Includes: **Everything in Team, plus SSO, compliance, and support terms**
   5. Access today: selected at build time

The close specification’s former three-sentence Team line is deliberately decomposed across the grid:

1. The published price and separate-cost boundary occupy Team’s Price cell.
2. The deployment location occupies Runs in.
3. The customer-provided model choice occupies Models.

This preserves every fact once and places the cost boundary directly beside the price without adding a sixth row or corrective prose later.

Operator’s 2,000-credit value is FINAL against the current registry but must be reopened during the build because `billing_design.md` identifies it as a shadow-period question.

## Access vocabulary and selection

Each plan receives exactly one independently evidenced state.

1. `purchasable now`

   Screen copy: **Self-serve · available now**

   Meaning: a visitor completes the live path and receives the plan without human action.

2. `manually provisioned`

   Screen copy: **Available now · set up by hand**

   Meaning: the plan is available on published terms without qualification or negotiation, but Victor performs activation.

3. `request access`

   Screen copy: **By request**

   Meaning: a real request path exists and approval or invitation follows, without implying an individualized sales process.

4. `sales-led`

   Screen copy: **Sales-led**

   Meaning: entry begins with a commercial conversation and depends on qualification, deal terms, or coordinated provisioning.

Walk every path in production using a fresh, non-founder identity. A route, page, or configured control is not proof. Testing ends only when the promised access exists. If no state fits, publication is blocked. Nothing is labeled “coming soon.”

## Branch A variant

1. The five-row grid remains unchanged.

2. Trial and Operator retain their independently verified access values.

3. Team and Enterprise both read:

   **Through the design-partner program below.**

4. The identical values prevent Enterprise from appearing to offer a larger-contract route around selection.

5. The following affirmed Q1 statement appears as plain body copy beneath the grid:

   > “Trial and Operator are hosted paths and are never subject to design-partner fit selection. Each shows its truthful current access method beside its price. Every customer-cloud deployment, including Enterprise, enters through the same design-partner selection process. A larger contract does not bypass the published refusal criteria.”

6. The statement receives no box, tint, icon, or distinct promotional treatment.

7. “Beside its price” means within the same plan column on wide screens and the same plan record on narrow screens. Changing the affirmed wording belongs to the Compact’s Q1 process.

## Branch B coordination

1. Section 3 owns:

   1. Plan prices
   2. Separate-cost boundaries
   3. Hosting locations
   4. Model terms
   5. Included entitlements
   6. Access classifications

2. Section 5 owns:

   1. The absence of external customer-cloud deployments
   2. The current operational sequence for proceeding with Team
   3. The Team contact action

3. Section 5 must not repeat the $29 price, separate-cost statement, provider choice, or “Sales-led” label.

## Required close-spec reconciliation

The close specification’s former prose requirement is replaced with:

> Team’s Price cell in section 3 reads “$29/agent/month · AWS infrastructure and model usage are separate.” Its Models cell reads “Your own: Amazon Bedrock or any provider you configure.” Section 5 does not repeat either fact.

Checklist item 6 becomes:

> Section 3’s Team column carries the $29 price and separate-cost boundary in its Price cell, and the customer-provided model terms in its Models cell. Section 5 depends on those facts and does not restate them.

This is a mechanical reconciliation after the plans round’s placement decision. No placement choice remains for the architect.

## Build-time gates

### Gate 1: Plan definition

1. Compare every value with `plans.py` and `billing_design.md`. Any disagreement reopens the affected cell.

2. Re-evaluate Operator’s 2,000-credit grant using the shadow-period evidence.

3. Confirm Trial and Operator still run in an AWS account controlled by Humanity Rules before retaining **Our AWS account**.

4. Confirm that Team’s $29 platform price excludes AWS infrastructure and model usage.

5. Confirm that Enterprise’s custom platform price also leaves AWS infrastructure and model usage with the customer. If contract structure changes this, reopen its Price cell rather than inheriting Team’s wording.

6. Confirm working Amazon Bedrock access for Team and Enterprise.

7. Confirm actual support for customer-configured providers beyond Bedrock. If that cannot be substantiated, both Models cells shrink to:

   **Your own: Amazon Bedrock**

8. Confirm Enterprise still includes SSO, compliance, and support terms.

9. Confirm the Trial clock still starts at first deployment and verify actual day-seven behavior. Do not add pause or wake copy.

### Gate 2: Access classification

1. Trial receives **Self-serve · available now** only after a fresh production identity completes signup, receives 500 credits, and reaches a deployed first agent without human action.

2. Operator receives **Self-serve · available now** only after all of the following are verified end to end:

   1. Billing migrations and billing page deployed
   2. California LLC filed
   3. Stripe live mode available
   4. Live Stripe price set to USD $39 monthly
   5. Checkout completes
   6. Signed webhook arrives
   7. Organization transitions to Operator
   8. The verified monthly credit grant posts

3. **Available now · set up by hand** requires a test organization to be activated through the real manual process at the published terms.

4. **By request** and **Sales-led** require both a working inbound path and a current handling process.

### Gate 3: Branch

1. Branch A requires Victor’s verbatim Q1 affirmation and identical Team and Enterprise program cells.

2. Branch B reserves the zero-external-deployment disclosure and Team operational sequence for section 5.

## Rejected decisions

1. Moving the cost boundary to section 5: it would correct the Team price after the visitor had already compared plans.

2. A sentence beneath the grid: it would duplicate the Team price and model facts while breaking the section’s no-extra-prose rule.

3. A sixth “Additional costs” row: it would separate the cost boundary from the price it qualifies and require misleading or empty hosted-plan cells.

4. Leaving the cost boundary implicit in **Your AWS account** and **Your own**: readers should not need prior AWS billing knowledge to understand that $29 is not all-in.

5. Four pricing cards, highlighted plans, ribbons, oversized prices, badges, and per-plan adjectives.

6. Credit-to-USD explanatory copy and provisional model-rate details.

7. Pause and wake Trial claims before those behaviors are verified.

8. Access instructions or contact links inside the grid.

The amended design keeps one ruled five-row grid. The separate-cost fact now qualifies Team’s price at first comparison, remains explicit for Enterprise, and does not recur in the close.

[END]

## Codex — conclusions (2026-08-11 00:44)

D2 is resolved. The separate-cost boundary belongs in section 3 because it changes how visitors compare Team’s $29 with Operator’s $39.

1. **Team Price cell:**  
   **$29/agent/month · AWS infrastructure and model usage are separate.**

2. **Enterprise Price cell:**  
   **Custom · AWS infrastructure and model usage are separate.**

3. The existing five-row grid survives unchanged. No sixth row or explanatory paragraph is added. The provider choice remains in Models:

   **Your own: Amazon Bedrock or any provider you configure**

4. The former close-spec sentence is decomposed across Price, Runs in, and Models. This keeps each fact once while presenting the cost boundary beside the price.

5. Section 5 remains responsible only for the current Team process, zero external customer-cloud deployments, and the contact action. It must not repeat the price, separate-cost boundary, model choice, or “Sales-led” label.

6. The close checklist receives a mechanical reconciliation stating that Team’s Price and Models cells carry these facts. The architect has no remaining placement decision.

7. Build-time verification now explicitly checks that AWS infrastructure and model usage remain outside Team and Enterprise platform pricing. Broad provider support must also be verified; otherwise the Models copy contracts to:

   **Your own: Amazon Bedrock**

8. Rejected placements were recorded: section 5, prose beneath the grid, a sixth costs row, and leaving the costs implicit.

No disagreements remain. The concrete artifact is the amended standalone section 3 screen specification in the preceding message, including exact copy, layout rules, branch behavior, access taxonomy, close-spec reconciliation, verification gates, and rejected alternatives.
