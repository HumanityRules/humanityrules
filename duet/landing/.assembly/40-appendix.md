
# Part 4 — What the search surfaced beyond the page

## Product tensions recorded for Victor (not design work, and not blocking either branch)

1. The cross-account role customers create is unscoped AdministratorAccess. Branch A states it plainly on the page as a pre-application disclosure. Branch B names the artifacts (stack, role) without auditing scope, bans all narrowing language, and gates the consent surface instead: the CloudFormation screen must make the role's permissions legible before authorization, or onboarding must disclose them. Role scoping is recorded as an unresolved product deficiency in the Ownership Walkthrough's write-up; if the role is narrowed before the page ships, the affected copy is re-verified and rewritten.
2. Integration credentials (OAuth refresh tokens, pasted provider keys) live in HumR's database. Same treatment: disclosed in Branch A, out of scope for Branch B's page copy, recorded as a product decision worth revisiting.
3. Verified strengths the search confirmed and the pages use: conversation history never touches the control-plane database; the Bedrock model path runs entirely in the customer's account with no HumR service on the wire; the customer's ECR holds the images; deleting one stack ends HumR's access.

## Assets Victor must produce or approve (consolidated; details in each spec's checklists)

1. A current portrait, with approval and alt text.
2. The signature statement and, in Branch A, the compact's obligations, produced through the voice loop: recorded speech, condensed without new vocabulary, read aloud, approved. Placeholder drafts in the specs never ship.
3. Figure captures: the `hermesvmendi01` control-plane card and the Sheets append/read-back action (or its recorded fallback), plus the credential-screen capture, each under its privacy pass. Explicit approval of the visible personal Gmail address.
4. The current-uses collection from the seven friends-and-family deployments under the eight-condition protocol, or omission of the line.
5. Branch A only: the signed questionnaire with every blank instantiated, an application form and review process that enact the published criteria, and operationally real response windows.
6. Build-time verification of every plan's access reality (Stripe live mode waits on the LLC filing) and of the Team onboarding and payment process.

## Kill ledger (why the page is not something else)

Every alternative killed on the way, with reasons, in the transcripts: Operator's Own Stack (gate-failed on evidence), One Real Run (no qualifying run verified; no degraded version permitted), Meet the Builder (load-bearing continuity drill unperformed and most expensive; its identity assets survive as modules in both branches), the Ownership Walkthrough (right audience, wrong instrument at today's shipped state; its disclosure discipline survives in Branch A), the live sandbox, F&F field notes as the page, the shipping ledger, manifesto-first, security-architecture-first, education-first, persona paths, problem/solution structures, momentum surfaces, waitlists and all scarcity theater, and dozens of section-level rejections recorded inside each spec.

## Process record

Tree: root concept catalog (5 concepts from 7+ territories, 8 kills) → five parallel development conversations (one killed at its gate) → judging (suspended itself on a missing bet, spawned the Compact's development, resumed) → two-branch verdict → four parallel screen-level conversations on the Card → this assembly → a final acceptance audit conversation (its findings folded in). Both agents ran with full repo access; every architectural claim on the pages was verified against code during the conversations, and the specs carry file-level anchors plus build-time re-verification checklists so facts cannot drift silently between spec time and ship time.
