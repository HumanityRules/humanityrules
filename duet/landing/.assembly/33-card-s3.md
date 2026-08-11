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

