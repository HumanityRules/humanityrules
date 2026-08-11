# facts

Claude: fable (xhigh) · Codex: gpt-5.6-sol (xhigh) · first: Codex

## Codex — turn 1 (2026-08-10 21:36)

I found two places where the canonical wording cannot descend unchanged.

1. **Reject “always-available.”** The repo defines an `always_on` entitlement, not an availability guarantee, and Trial is explicitly not always-on. The screen should say “always-on” only for Operator, Team, and Enterprise.

2. **The credential UI has changed since the parent conversation.** The active tab is still “Provider Keys,” but the page heading is now “Shared credentials,” and the unified interface supports both pasted keys and connected logins. The fact and capture must reflect the current screen, not the earlier key-only account.

The repo supports this exact screen structure:

1. Section heading: **Product facts**.

2. One continuous label-and-value account, ordered:

   1. Runtime
   2. Credentials
   3. Deployment
   4. Harness

   These are rows in one account, not separate feature cards. There is no introductory paragraph, iconography, reassurance badge, or closing explanation.

3. The credential figure belongs inside the account, immediately after the Credentials value and before Deployment. It is subordinate evidence for that row, not a second product-proof section.

4. The Hermes provenance is the final row of the account. It receives no descriptive follow-up.

5. The current-uses line sits below the completed account as a single labeled line. It is not another row because it reports observed use rather than product construction. If verification fails, the entire line disappears and Harness becomes the section’s last element.

My proposed copy is:

**Runtime — FINAL**

> Each agent runs in a hosted runtime, independent of the user’s laptop. Its persistent filesystem retains conversation state and its workspace across deployments. Operator, Team, and Enterprise agents are always on; Trial includes one agent and seven days of runtime.

This is grounded in the product synopsis, the plan registry’s `always_on` and `trial_runtime_days` fields, the Hermes persistent-root runner, the EFS checkpoint configuration, and serialized task replacement. I would not say “remembers everything,” “never stops,” “available 24/7,” or “survives every failure.”

**Credentials — FINAL**

> An organization administrator adds a provider key or connects a login once, then shares it with everyone in the organization, a workspace, or one user. Members’ agents use the applicable credential without per-user setup. Members cannot view, copy, or export its value.

That is the limit of the shipped interface. It must not become “scoped to a resource,” “attached to this agent,” “granted on this date,” “encrypted in a vault,” or “the credential used for the action above.” The UI proves none of those. The platform-only “All customers” scope should also stay out of customer-facing copy.

**Deployment — FINAL**

> Trial and Operator are hosted by HumR. Team and Enterprise deploy into the customer’s AWS account.

The plan registry states exactly this boundary. I would not add “inside your perimeter,” “nothing leaves your account,” or data-residency consequences here; those are separate claims requiring separate verification.

**Harness — FINAL**

> Built on the open-source Hermes Agent harness.

“Built on the best open-source agent” is dead. So are portability, inspectability, independence, and affiliation arguments in this row. Identification only.

**Current uses — UNVERIFIED PLACEHOLDER; DO NOT SHIP**

> Current uses: [task category], [task category], and [task category].

The categories must use the deployers’ ordinary verb-and-object language. No AI should expand “help with work” into a polished capability taxonomy.

I propose this verification protocol:

1. Victor performs the collection; no agent or copywriter infers categories from installed skills, connected providers, prompts, thread titles, or what Hermes is capable of doing.

2. Victor contacts the owner of each of the seven live friends-and-family deployments using the same request: identify tasks completed with the deployment during the 30 calendar days before copy freeze and show the corresponding session or resulting artifact.

3. A category qualifies only when:

   1. The work occurred on one of those seven deployments.
   2. At least one completed instance falls inside the 30-day window.
   3. Victor sees the dated request/session and a substantive result or external artifact.
   4. It was genuine use, not onboarding, a product test, a demonstration, or a task performed for the landing page.
   5. The label describes the work at recognizable verb-and-object resolution.
   6. The label can be published without identifying the user, employer, client, repository, account, or private subject.
   7. The deployment owner approves publication of the category in aggregate.

4. Victor records deployment alias, evidence date, evidence type, owner-supplied wording, normalized public wording, and consent. He need not retain private task content.

5. The line ships only if at least two distinct categories from at least two distinct deployments qualify. It lists only qualifying categories; a nonresponse contributes nothing.

6. Omit the entire line if the threshold is not met by copy freeze, if any listed category rests on recollection alone, or if anonymizing a category turns it into “research,” “productivity,” “automation,” or similarly empty capability language. Never ship placeholders, “various tasks,” or a weakened line.

For the credential asset, I would specify:

1. Source the current shipped HumR control-plane screen at `/integrations/org/provider-keys/`, authenticated as an organization administrator.

2. Show the Integrations context, active Provider Keys tab, Shared credentials heading, the three shipped statements—Provision once, Used automatically, Never exposed—and the populated table.

3. The table must show at least one real credential row and the columns Provider, Type, Shared with, Secret, and Added by. Do not create a marketing-only credential or alter its scope to improve the capture.

4. No modal is open. No annotations, callouts, compositing, or explanatory overlay appear inside the figure.

5. **Caption — FINAL**

   > Shared credentials in HumR. Each row names the provider, credential type, sharing scope, and the administrator who added it; the stored value is masked.

6. Privacy pass:

   1. Every secret cell shows only the interface’s fixed bullet mask.
   2. No device code, verification URL, token fragment, browser autofill, toast, or open form is visible.
   3. Every provider name, workspace name, user identity, organization name, and administrator address visible in the frame has explicit publication approval.
   4. No unrelated navigation item exposes an organization, environment, or account identifier.
   5. The asset contains no embedded location or account metadata.
   6. The frame requires no blur or censor box. If cropping cannot remove unapproved material without making the screen incoherent, the asset fails and must be recaptured from a naturally publishable real state.
   7. A second person inspects the final exported asset at full resolution.

The build-time checklist for this section should require:

1. Reverify `always_on`, Trial runtime, and plan hosting boundaries in the live plan registry.
2. Reverify that the deployed Hermes template still persists conversation state and workspace across redeployments.
3. Reverify the credential screen’s title, types, sharing scopes, admin gate, masking, and member-access language.
4. Confirm that no credential sentence implies resource scope, per-agent attachment, encryption, a grant date, or correspondence with the proof action.
5. Capture the current shipped interface and complete the two-person privacy pass.
6. Reverify the Hermes Agent name, open-source status, and destination of its link.
7. Run the current-uses protocol within 30 days of copy freeze or omit the line.
8. Compare every visible sentence against the repo evidence; delete any adjective whose work can be done by a named mechanism.
9. Confirm the account remains in Runtime → Credentials and figure → Deployment → Harness order, with Current uses last and optional.

My main uncertainty is the current-uses threshold. I chose two categories across two deployments because one singleton use is technically true but reads like an anecdote being inflated into range. Attack that threshold, and also attack whether the credential figure should expose the Added by column at all—the old cross-screen Victor-email “rhyme” is not valuable enough to override a clean privacy pass.

## Claude — turn 2 (2026-08-10 21:42)

I verified every fact in your draft against the repo before responding. Results first, then the disagreements.

**What survives verification.** Plan registry (`humanityrules_app/services/billing/plans.py:81-126`): Trial `always_on=False, trial_runtime_days=7, max_agents=1`; Operator/Team/Enterprise `always_on=True`; Team/Enterprise `customer_cloud=True`. Your correction on always-on is right. The credential screen (`shared_keys.html`): heading "Shared credentials", tab "Provider Keys", URL `/integrations/org/provider-keys/`, columns Provider/Type/Shared with/Secret/Added by, the three statements with exactly your member-access language ("Members can't view, copy, or export the value"), scopes Everyone / Workspace · X / User · X, `require_org_admin` on every route, both Key and Login kinds. Your Credentials row is near-verbatim descent from shipped pixels — it's the strongest of the four. Upstream: "Hermes Agent", MIT, Nous Research, all confirmed in the vendored LICENSE and README.

Now the pushback.

**1. Your Runtime row smuggles the plans table into the facts account, and does it asymmetrically.** "Trial includes one agent" names Trial's cap — but Operator is also `max_agents=1`. A reader who sees "Trial includes one agent" next to "Operator agents are always on" infers Operator means multiple agents. That's a selective fact, the exact move this page forbids. And once you fix it symmetrically you've reproduced section 3, because always-on, runtime days, and agent counts are plan entitlements — the canonical spec assigns "hosting/model terms, limits" to Plans, and the billing page already ships this vocabulary ("One always-on agent", "7 days of runtime · 1 agent", `billing.html:49,94`). Note also that `always_on` appears in zero enforcement code — it's a registry flag and billing copy. What makes the claim *true* is construction: agents run as ECS services that stay up when nobody is connected; no scale-to-zero path exists (`on_demand_allowed=False` on every plan). So state the construction in Facts and leave the entitlement split to Plans:

> Each agent runs in its own runtime on HumR-managed infrastructure and keeps running when no one is connected. Its persistent filesystem retains conversation history and workspace across redeployments.

Hmm — "HumR-managed" is wrong for Team/Enterprise. Second attempt, and my actual proposal: **"Each agent runs in its own cloud runtime, independent of anyone's laptop, and keeps running when no one is connected. Its persistent filesystem retains conversation history and workspace across redeployments."** This is true for every tier while the agent exists; the 7-day trial duration is priced in section 3 where it already lives. If you insist the always-on split must appear in Facts (the parent's "persistence is asserted in the facts section" argument), then it must be symmetric — and a symmetric version *is* the plans table, which proves it doesn't belong here.

**2. "Hosted" does two jobs in your account.** Runtime says "hosted runtime" (meaning: not your laptop); Deployment says "hosted by HumR" (meaning: not your AWS). Same word, two boundaries, three rows apart. The registry's own comment hands you the fix: "Trial and Operator run on HumR's cloud." So Deployment becomes: **"Trial and Operator run in HumR's cloud. Team and Enterprise deploy into the customer's AWS account."** — and "hosted" appears nowhere, killing the collision.

**3. "Harness" is not the product's name.** Upstream self-identifies as "Hermes Agent," "the self-improving AI agent." Nowhere in its README or site does it call itself a harness — that's our internal vocabulary. Identification-only discipline means using the identified thing's own name: **"Built on the open-source Hermes Agent."** FINAL from my side. Two dependencies to record: the link target must be the official `hermes-agent.nousresearch.com` (the current footer declares it official; the current hero links `get-hermes.ai` — pick one, verify live at build); and the MIT/Nous/non-affiliation disclaimer currently lives in the footer — the new page's section 6 must keep it, otherwise this row's link becomes the page's only Nous touchpoint with no trademark hygiene anywhere.

**4. Your privacy-pass criterion 6.1 fails legitimate rows.** The template renders the mask *plus a green confirmation check* when the credential has `validated_at`/`connected_at`, and an em dash — no mask at all — when unconfigured. As written, a healthy confirmed row fails your pass and an unconfigured row falsifies the caption's "the stored value is masked." Fix: require every visible row to be configured (mask present), and permit the interface's own confirmation mark beside it. Separately, your spec never mentions the Actions column: every row carries visible "Edit" and red "Delete" buttons (`shared_keys.html:100-110`). Your caption names five columns; the frame shows six. State explicitly that the actions column ships in-frame — otherwise a builder "fixes" the mismatch by cropping the table's right edge, which is compositing by scissors.

**5. The capture has an org-selection trap you haven't specified.** `list_display_rows` appends platform-scoped rows ("All customers") only for the platform-owner org — which is precisely the org most likely to have a populated table today: Victor's. You correctly ban "All customers" from copy; the same ban must apply to pixels, so the capture must come from a **non-platform org** whose rows show only Everyone/Workspace/User. Combined with your (correct) no-manufactured-credential rule, this may be capture-blocking: if no friends-and-family org has organically added a shared credential, the figure has no legal source state and must wait for one. That contingency belongs in the asset spec explicitly, with the fallback named — the empty-state screen is *not* an acceptable substitute, because a table with zero rows proves provisioning exists but not sharing.

**6. Added by: your question is malformed.** Your own rule 4 bans compositing, so the column ships if the screen ships — there is no "expose it or not" decision, only a consent decision about the identity it displays (`created_by.username`, an email-shaped string). Victor consenting to his own address is trivial, and the cross-screen rhyme the parent spec explicitly permits as an unremarked detail comes free. Keep it and stop treating it as a choice.

**7. Current-uses threshold: your spread requirement guards the wrong risk.** Keep ≥2 categories — one category is an anecdote, agreed. But requiring them from ≥2 *deployments* suppresses true, verified information (three real categories from one heavy user) to manage an inference the line never asserts — and refusing to publish verified truth for optics is the genre behavior this page refuses. The risk that actually matters is provenance: if a qualifying deployment is operated by Victor himself, "current uses" is self-testimony wearing an observation costume. So: drop the two-deployment spread, add an eligibility rule — **founder-operated deployments contribute nothing to this line** (a no-op if none of the seven is his; load-bearing if one is — the protocol shouldn't depend on knowing which). Also, your threshold sentence "two distinct categories from two distinct deployments" is ambiguous as written — it doesn't say whether the categories must come from *different* deployments or merely that two deployments each contributed something. Moot if you accept my version; must be tightened if you don't.

Everything else in your protocol — Victor collects, 30-day window, dated evidence sighted, no inference from installed skills, owner consent, the omission rules including the "anonymization produces empty capability language → omit" clause — I verified has no repo contradiction and I have no quarrel with it. Your caption is fine except that "the administrator who added it" should survive contact with the pixels: the column header says "Added by" and renders a username; "the administrator who added it" is accurate since the add route is admin-gated — confirmed, keep it.

Resolve these three and I think we're at screen-spec: the Runtime row's shape (construction-only, my position, vs. entitlement-split), the harness noun, and the current-uses spread rule.

## Codex — turn 3 (2026-08-10 21:44)

I accept all three substantive corrections.

1. **Runtime — FINAL**

> Each agent runs in a separate cloud runtime, independent of anyone’s laptop, and keeps running when no one is connected. A persistent filesystem retains its conversation state and workspace across redeployments.

Construction belongs here; plan entitlements do not. “Conversation state” is preferable to “history” because it matches the deployment code’s own account. Trial duration, agent limits, and the always-on entitlement remain exclusively in Plans.

2. **Deployment — FINAL**

> Trial and Operator run in HumR’s cloud. Team and Enterprise deploy into the customer’s AWS account.

“Hosted” disappears from the section.

3. **Harness — FINAL**

> Built on the open-source Hermes Agent.

Agreed: “harness” is HumR’s internal architectural noun, not Hermes’s identification. “Hermes Agent” is the linked text. Build dependencies:

   1. Verify the official destination at build time; current evidence favors `hermes-agent.nousresearch.com`.
   2. Retain the factual MIT, Nous Research, and non-affiliation statement in section 6. Do not burden this row with it.

4. **Current uses**

I accept dropping deployment spread and adding founder exclusion. The exact rule is:

   1. At least two distinct qualifying task categories must exist.
   2. Both categories may come from the same eligible deployment.
   3. A founder-operated deployment contributes nothing.
   4. “Founder-operated” means Victor supplied the qualifying work requests or the agent performed Victor’s work. Victor administering, onboarding, or maintaining someone else’s deployment does not disqualify that owner’s use.
   5. If fewer than two categories survive evidence, recency, consent, privacy, and non-founder checks, omit the line.

This protects provenance without suppressing genuine range from one heavy user.

5. **Credential figure corrections accepted**

   1. Source only from a populated, non-platform organization, so “All customers” cannot appear.
   2. Every visible row must be configured and therefore show the fixed mask.
   3. The interface’s confirmation check may appear beside the mask.
   4. The entire table ships in-frame, including Added by and Actions with Edit/Delete.
   5. Every identity shown in Added by requires consent; the column itself is not optional.
   6. No row may be created or reshaped for the capture.
   7. If no populated, privacy-cleared non-platform source exists, there is no substitute asset. The empty state, cropped platform table, and manufactured row are rejected; the section has not passed its asset gate.

The caption remains:

> Shared credentials in HumR. Each row names the provider, credential type, sharing scope, and the administrator who added it; the stored value is masked.

With these resolutions, the account is locked as:

1. Runtime — construction and persistence.
2. Credentials — provision, recipient scopes, automatic use, member visibility limits.
3. Credential figure and caption.
4. Deployment — HumR cloud versus customer AWS.
5. Hermes — identification only.
6. Current uses — optional, evidence-gated observation.

No unresolved copy decision remains. Write the standalone screen spec.

## Claude — turn 4 (2026-08-10 21:45)

"Conversation state" confirmed as the deployer's own vocabulary (`appconfig.py:231`, `deploy_app.py:632`). Codex's justification holds. Here is the consolidated standalone spec — everything below was agreed in this conversation; nothing new is introduced.

---

# Screen spec — Plain Product Card, section 2: Product facts

All copy marked FINAL is locked. Every stated fact carries its repo anchor; the build-time checklist re-verifies each anchor before ship. No pixel values or medium decisions appear here by design.

## 1. Element inventory and layout

1. Section heading: **Product facts**.
2. One continuous label-and-value account of four rows, in this order of importance:
   1. **Runtime**
   2. **Credentials**
   3. **Deployment**
   4. **Harness**

   These are rows of a single account, not feature cards. No introductory paragraph, no iconography, no reassurance badge, no closing explanation. Nothing on the page argues for the page's honesty.
3. The credential figure sits inside the account, immediately after the Credentials value and before the Deployment row. It is subordinate evidence for that one row, not a second proof section.
4. Harness is the account's final row and receives no descriptive follow-up.
5. The current-uses line sits below the completed account as one labeled line. It is not an account row: it reports observed use, not product construction. If it fails its gate (§4), the entire line is omitted and Harness ends the section.

## 2. Copy

**Runtime — FINAL**

> Each agent runs in a separate cloud runtime, independent of anyone's laptop, and keeps running when no one is connected. A persistent filesystem retains its conversation state and workspace across redeployments.

Anchors: ECS-service construction with no scale-to-zero path (`on_demand_allowed=False` on every plan, `services/billing/plans.py`); persistent root (`template_repos/hermes_agent/humr_runtime/persistent-root-runner.sh`); "conversation state" is the deployer's own term (`services/infra_customer/appconfig.py:231`).

Exclusions: no plan entitlements in this row. Always-on, trial duration, and agent counts live exclusively in section 3 (Plans) — stating any of them here reproduces the plans table, and stating some of them is a selective fact. Never: "remembers everything," "never stops," "available 24/7," "survives every failure."

**Credentials — FINAL**

> An organization administrator adds a provider key or connects a login once, then shares it with everyone in the organization, a workspace, or one user. Members' agents use the applicable credential without per-user setup. Members cannot view, copy, or export its value.

Anchors: `templates/humanityrules_app/integrations/shared_keys.html` (scopes at line 84, member-access sentence at line 51, key/login kinds at 76–80); admin gate `require_org_admin` in `views/integrations/org_shared_keys.py`.

Exclusions: no "scoped to a resource," "attached to this agent," "granted on this date," "encrypted," "vault," or any correspondence claim with section 1's proof action — the shipped interface proves none of those. The platform-only "All customers" scope appears in no customer-facing copy.

**Credential figure** — placed here; full asset spec in §3.

**Deployment — FINAL**

> Trial and Operator run in HumR's cloud. Team and Enterprise deploy into the customer's AWS account.

Anchor: `customer_cloud` flags and the registry's own comment ("Trial and Operator run on HumR's cloud"), `services/billing/plans.py:74-126`. The word "hosted" appears nowhere in the section. Exclusions: no "inside your perimeter," "nothing leaves your account," no data-residency consequences — separate claims requiring separate verification.

**Harness — FINAL**

> Built on the open-source Hermes Agent.

"Hermes Agent" is the linked text; destination `hermes-agent.nousresearch.com` pending build-time verification. Anchors: upstream name and MIT license in `template_repos/hermes_agent/vendor/hermes-agent/README.md` and `LICENSE` (Nous Research). Exclusions: "harness" (HumR-internal noun, not Hermes's identification), "best," portability, inspectability, independence, and affiliation arguments. The factual MIT / Nous Research / non-affiliation statement lives in section 6's footer, never in this row.

## 3. Credential figure — asset spec

1. **Source.** The shipped control-plane screen at `/integrations/org/provider-keys/`, authenticated as an organization administrator of a **populated, non-platform organization**. The platform-owner org is ineligible: its list includes "All customers" rows (`shared_credential_store.list_display_rows`), a scope no customer can produce.
2. **Content.** Integrations context, active "Provider Keys" tab, "Shared credentials" heading, the three shipped statements (Provision once / Used automatically / Never exposed), and the populated table with columns Provider, Type, Shared with, Secret, Added by — plus the Actions column with its Edit and Delete controls. The whole table ships in-frame; cropping columns to match the caption is compositing by scissors and is rejected.
3. **Row integrity.** At least one real credential row. Every visible row must be configured (fixed bullet mask present in Secret); the interface's own confirmation check may appear beside the mask. No row is created, edited, re-scoped, or otherwise reshaped for the capture. Both credential kinds appear only if the real state has both.
4. **State.** No modal open. No annotations, callouts, compositing, or explanatory overlay inside the figure.
5. **Caption — FINAL**

   > Shared credentials in HumR. Each row names the provider, credential type, sharing scope, and the administrator who added it; the stored value is masked.

6. **Privacy pass.**
   1. Every visible row's Secret cell shows the interface's fixed mask (with or without the confirmation check). No em-dash unconfigured row in frame.
   2. No device code, verification URL, token fragment, browser autofill, toast, or open form visible.
   3. Every provider name, workspace name, user identity, organization name, and Added-by address in frame has explicit publication approval. The Added-by column itself is not optional — only the consent about the identity it shows is a decision.
   4. No "All customers" badge anywhere in frame.
   5. No unrelated navigation item exposes an organization, environment, or account identifier lacking approval.
   6. The exported asset carries no embedded location or account metadata.
   7. The frame requires no blur or censor box. If cropping cannot remove unapproved material while keeping the screen coherent, the asset fails.
   8. A second person inspects the final export at full resolution.
7. **Failure mode.** If no populated, privacy-cleared, non-platform source state exists, there is no substitute asset: the empty state, a cropped platform-org table, and a manufactured row are all rejected. The section has not passed its asset gate and does not ship the figure.

## 4. Current-uses line

**Form — UNVERIFIED PLACEHOLDER; DO NOT SHIP**

> Current uses: [task category], [task category], and [task category].

Two-category minimum form: "Current uses: [task category] and [task category]." Categories use the deployers' ordinary verb-and-object language; no AI expands owner wording into a capability taxonomy.

**Verification protocol.**

1. Victor performs the collection. No agent or copywriter infers categories from installed skills, connected providers, prompts, thread titles, or Hermes's capabilities.
2. Victor contacts the owner of each of the seven live friends-and-family deployments with the same request: identify tasks completed with the deployment during the 30 calendar days before copy freeze, and show the corresponding session or resulting artifact.
3. A category qualifies only when:
   1. The work occurred on one of the seven deployments.
   2. At least one completed instance falls inside the 30-day window.
   3. Victor sees the dated request or session and a substantive result or external artifact — recollection alone never qualifies.
   4. It was genuine use: not onboarding, a product test, a demonstration, or work performed for the landing page.
   5. The label describes the work at recognizable verb-and-object resolution.
   6. The label can be published without identifying the user, employer, client, repository, account, or private subject.
   7. The deployment owner approves publication of the category in aggregate.
   8. The deployment is not founder-operated for that work. "Founder-operated" means Victor supplied the qualifying requests or the agent performed Victor's work; Victor administering, onboarding, or maintaining someone else's deployment does not disqualify that owner's use.
4. Victor records, per category: deployment alias, evidence date, evidence type, owner-supplied wording, normalized public wording, and consent. He need not retain private task content.
5. **Ship gate:** at least two distinct qualifying categories. Both may come from the same eligible deployment. The line lists only qualifying categories; a nonresponse contributes nothing.
6. **Omission rule:** omit the entire line if fewer than two categories survive evidence, recency, consent, privacy, and non-founder checks by copy freeze, or if anonymizing a category reduces it to empty capability language ("research," "productivity," "automation"). Never ship placeholders, "various tasks," or a weakened line. When omitted, Harness is the section's last element.

## 5. Build-time checklist

1. Reverify `always_on`, `trial_runtime_days`, `max_agents`, and `customer_cloud` per plan in the live registry (`services/billing/plans.py`); confirm section 2 still contains none of these entitlements and section 3 contains all of them.
2. Reverify construction behind the Runtime row: agents deploy as continuously running ECS services with no scale-to-zero path, and the persistent root retains conversation state and workspace across a real redeployment.
3. Reverify the credential screen: heading, tab label, URL, three statements, column set, scope labels, key/login kinds, admin gate, mask rendering, and the member-access sentence.
4. Confirm no credential sentence implies resource scope, per-agent attachment, encryption, a grant date, or correspondence with section 1's proof action.
5. Capture per §3 from a qualifying non-platform org; run the two-person privacy pass; on failure, ship the section without the figure per §3.7.
6. Reverify "Hermes Agent" as upstream's name, its MIT/open-source status, and that the link destination is live and official; confirm the non-affiliation statement is present in section 6.
7. Run the current-uses protocol inside the 30-day window or omit the line per §4.6.
8. Sweep every visible sentence against its repo anchor; delete any adjective whose work a named mechanism can do.
9. Read the assembled section against the AI-generated-copy tells; the never-AI-generated criterion is a hard gate, not a preference.
10. Confirm final order: Runtime → Credentials → figure → Deployment → Harness, current-uses line last and optional.

---

That's the consolidation — no new decisions, one added verification (conversation state), and your §4.4 founder-operated definition carried verbatim. If it matches your record of what we agreed, close it on your turn.

## Codex — turn 5 (2026-08-10 21:46)

It matches except for three internal inconsistencies. Apply these exact corrections:

1. **Runtime anchor:** remove `on_demand_allowed=False` as evidence of construction; it is an entitlement flag, not enforcement. Anchor continuous execution to the ECS service’s `desired_count=1` in `services/infra_customer/deploy_app.py`, plus the absence of any connection-driven scale-to-zero path.

2. **Checklist item 1:** section 2 does contain `customer_cloud` through the Deployment row. Replace the final clause with:

   > Confirm `always_on`, `trial_runtime_days`, and `max_agents` remain exclusively in section 3; section 2 states `customer_cloud` only as the HumR-cloud/customer-AWS deployment boundary.

3. **Asset failure:** checklist item 5 contradicts §3.7 by permitting the section to ship without its required figure. Replace both conclusions with:

   > If no qualifying capture exists, the section fails build review. Do not substitute another asset or ship the section without the figure.

Everything else matches the agreed record. The screen spec is closed with those corrections.

[END]

## Codex — conclusions (2026-08-10 21:46)

Section 2 is decision-complete. The standalone screen spec is Claude’s final consolidation, amended by Codex’s three closing corrections.

1. Final structure:

   1. Product facts heading.
   2. One account ordered Runtime → Credentials → credential figure → Deployment → Harness.
   3. Optional Current uses line last.
   4. No cards, introduction, icons, reassurance copy, or closing argument.

2. Locked copy:

   - **Runtime:** “Each agent runs in a separate cloud runtime, independent of anyone’s laptop, and keeps running when no one is connected. A persistent filesystem retains its conversation state and workspace across redeployments.”
   - **Credentials:** “An organization administrator adds a provider key or connects a login once, then shares it with everyone in the organization, a workspace, or one user. Members’ agents use the applicable credential without per-user setup. Members cannot view, copy, or export its value.”
   - **Deployment:** “Trial and Operator run in HumR’s cloud. Team and Enterprise deploy into the customer’s AWS account.”
   - **Harness:** “Built on the open-source Hermes Agent.”

3. Important copy boundaries:

   1. “Always-available” was rejected; HumR defines an always-on entitlement, not an uptime guarantee.
   2. Always-on status, trial duration, and agent limits remain in Plans.
   3. “Hosted” was removed to avoid conflating remote runtime with HumR-owned infrastructure.
   4. Credential copy makes no claim about encryption, vaulting, resource scope, per-agent attachment, grant dates, or correspondence with the proof action.
   5. “Harness” was removed from visible Hermes copy because it is HumR’s internal architectural term.
   6. Hermes Agent is linked by name; its MIT, Nous Research, and non-affiliation statement remains in the footer.

4. Credential figure:

   1. Must come from the shipped `/integrations/org/provider-keys/` screen in a populated, non-platform organization.
   2. Shows the complete interface and table, including Added by and Edit/Delete controls.
   3. Requires at least one organic, configured credential row; no row may be manufactured or reshaped for the capture.
   4. “All customers,” unconfigured rows, private identifiers without consent, open forms, codes, tokens, blur, and censor boxes are disallowed.
   5. A second person inspects the export at full resolution.
   6. If no qualifying capture exists, the section fails build review. It does not ship without the figure or with a substitute.

5. Locked figure caption:

   > Shared credentials in HumR. Each row names the provider, credential type, sharing scope, and the administrator who added it; the stored value is masked.

6. Current-uses protocol:

   1. Victor contacts all seven friends-and-family deployment owners using the same request.
   2. Eligible work must have completed during the 30 days before copy freeze and be supported by a dated session plus a substantive result or artifact.
   3. Tests, onboarding, demonstrations, landing-page work, recollection-only claims, and founder-operated work do not qualify.
   4. Owners approve aggregate publication; labels remain ordinary verb-and-object descriptions.
   5. At least two distinct categories must qualify, though both may come from one eligible deployment.
   6. Otherwise the entire line is omitted. No placeholders or generalized capability language ships.

7. Unverified placeholder form:

   > Current uses: [task category], [task category], and [task category].

8. Final factual corrections:

   1. Continuous execution must be anchored to the ECS service’s `desired_count=1` and absence of a connection-driven scale-to-zero path—not `on_demand_allowed=False`, which is only an entitlement flag.
   2. `always_on`, `trial_runtime_days`, and `max_agents` remain exclusive to Plans; section 2 uses `customer_cloud` only for the deployment boundary.
   3. Failure to produce the required credential figure blocks the section.

9. No design disagreement remains unresolved. Remaining work is execution: collect current-use evidence, obtain the credential capture and consent, verify the official Hermes link and footer attribution, and rerun the build-time checklist against the live repo and deployed product.

## Victor (2026-08-11 00:42)

Acceptance audit finding, BLOCKING — defect D1 in ../../audit/conversation.md, read it first. The credential figure's source state is verified absent: production contains ZERO IntegrationSharedCredential rows across all 12 organizations, platform org included, verified twice independently on 2026-08-11. Org-scope credential sharing has never been used in production; the F&F deployments run on user-level credentials only. Your §3.7 no-figureless-section rule was written on the assumption that a qualifying capture plausibly existed; that assumption is falsified, so revisiting §3.7 is new-evidence redesign, not a kill revival. Constraints that stand: the empty state, the platform-org crop, and a manufactured row remain killed; the Credentials row copy itself remains true of the shipped interface. Settle honestly: can section 2 be truthful and complete without the figure? If yes, respec the section accordingly and issue an amended standalone spec. If no honest presently-available resolution exists, say so plainly — Branch B's feasibility premise then goes back to judging.

## Claude — turn 6 (2026-08-11 00:47)

Settled. My answer to the audit's question: **yes — section 2 can be truthful and complete without the figure, and the figure should die as a kill with cause, not a deferral.** Three grounds:

**1. The figure could never have been truthful.** The audit's zero-row fact means every possible capture path was already killed on its own demerits: a populated table requires manufactured state (killed), the platform org's table shows a scope no customer can have (killed), and the empty state proves provisioning without sharing (killed). But the deeper point: a populated table would have *visually asserted adoption that does not exist*. Production's credential mode is 100% user-level (`IntegrationUserCredential`, zero `IntegrationSharedCredential` rows). The no-manufacture rule didn't fail to find an asset — it correctly detected that this asset was unproducible without lying. That is a cause-of-death, not a waiting room.

**2. Figureless is the section's native mode, not a degradation.** Both specs on record agree the figure was "subordinate evidence for that row, not a second product-proof section." The Card's proof unit is section 1's two figures, untouched. The page's most important claim — persistence — is already asserted without pixels; that trade is the Card's judged identity. Removing the figure makes the account uniform: four rows, all asserted, all repo-anchored at build. The structural parallel to One Real Run fails precisely here: ORR's missing evidence was its load-bearing proof unit; this figure was not. So the verdict's Branch B premises hold under the amendment — no evidentiary experiment remains (current-uses stays optional with a clean omission path), and judging need not reopen. Honest cost accounting: the trust-surface sentence ("members cannot view, copy, or export its value") is now asserted rather than shown, and the section loses its only figure. That loss is real, marginal, and of the same kind the Card already accepts for its #1 claim.

**3. The zero-row fact exposes a selectivity defect in the Credentials row itself — I'm reopening one FINAL.** The row as locked describes org-shared provisioning as *the* credential story, while the mechanism carrying every production credential — members connecting their own — appears nowhere. When we locked it, a populated sharing table was presumed plausible; now we know the row's only fact is a zero-use capability and its silence hides the 100%-use mode. On a page whose identity is representative reality, that's backwards, and the fix is cheap and code-anchored (`IntegrationUserCredential`, `models.py:1454`: user-owned logins and keys, per member per agent; connect flows in `views/integrations/provider_*.py`). Note the verdict already set this precedent for Deployment: state the construction, disclose non-use where it's a purchasable tier. For a feature, the fix is ordering — lead with the exercised mode, then the capability. This amendment is severable: if you reject it, the figure-kill resolution stands with the original row.

Here is the amended standalone spec.

---

# Screen spec — Plain Product Card, section 2: Product facts (AMENDED 2026-08-11, resolves audit defect D1)

All copy marked FINAL is locked. Every stated fact carries its repo anchor; the build-time checklist re-verifies each anchor before ship. No pixel values or medium decisions appear here by design.

## 1. Element inventory and layout

1. Section heading: **Product facts**.
2. One continuous label-and-value account of four rows, in this order of importance:
   1. **Runtime**
   2. **Credentials**
   3. **Deployment**
   4. **Harness**

   These are rows of a single account, not feature cards. No introductory paragraph, no iconography, no reassurance badge, no closing explanation. Nothing on the page argues for the page's honesty.
3. The section contains **no figure** (see §3, kill record). Every row is an assertion verified at build time against the repo; no row carries pixel evidence.
4. Harness is the account's final row and receives no descriptive follow-up.
5. The current-uses line sits below the completed account as one labeled line. It is not an account row: it reports observed use, not product construction. If it fails its gate (§4), the entire line is omitted and Harness ends the section.

## 2. Copy

**Runtime — FINAL**

> Each agent runs in a separate cloud runtime, independent of anyone's laptop, and keeps running when no one is connected. A persistent filesystem retains its conversation state and workspace across redeployments.

Anchors: ECS service with `desired_count=1` (`services/infra_customer/deploy_app.py:656`) and the absence of any connection-driven scale-to-zero path; persistent root (`template_repos/hermes_agent/humr_runtime/persistent-root-runner.sh`); "conversation state" is the deployer's own term (`services/infra_customer/appconfig.py:231`).

Exclusions: plan-level always-on entitlements, trial duration, and agent counts live exclusively in section 3 — stating any of them here reproduces the plans table, and stating some of them is a selective fact. Never: "remembers everything," "never stops," "available 24/7," "survives every failure."

**Credentials — FINAL (amended this round; replaces the prior FINAL)**

> Each member connects their own provider logins and keys to their agent. An organization administrator can also add a provider key or connect a login once and share it with everyone in the organization, a workspace, or one user; members' agents use it without per-user setup, and members cannot view, copy, or export its value.

Anchors: user-owned credentials `IntegrationUserCredential` (`models.py:1454` — per member, per agent, logins and keys) with connect flows in `views/integrations/provider_*.py`; org sharing in `templates/humanityrules_app/integrations/shared_keys.html` (scopes at line 84, member-access sentence at line 51, key/login kinds at 76–80); admin gate `require_org_admin` in `views/integrations/org_shared_keys.py`.

Ordering rule: the member-connected mode leads because it is the base construction; sharing is stated as capability ("can also"), not routine. Exclusions: no "scoped to a resource," "attached to this agent," "granted on this date," "encrypted," "vault," or any correspondence claim with section 1's proof action — the shipped interface proves none of those. The platform-only "All customers" scope appears in no customer-facing copy. No usage or adoption claim of the sharing capability, in either direction.

**Deployment — FINAL**

> Trial and Operator run in HumR's cloud. Team and Enterprise deploy into the customer's AWS account.

Anchor: `customer_cloud` flags and the registry's own comment (`services/billing/plans.py:74-126`). The word "hosted" appears nowhere in the section. Exclusions: no "inside your perimeter," "nothing leaves your account," no data-residency consequences. (Audit note N1: the deliberate overlap with section 3's Runs-in row is a recorded exception to each-fact-once.)

**Harness — FINAL**

> Built on the open-source Hermes Agent.

"Hermes Agent" is the linked text; destination `hermes-agent.nousresearch.com` pending build-time verification. Anchors: `template_repos/hermes_agent/vendor/hermes-agent/README.md` and `LICENSE` (MIT, Nous Research). Exclusions: "harness," "best," portability, inspectability, independence, and affiliation arguments. The MIT / Nous Research / non-affiliation statement lives in section 6's footer, never in this row (audit note N2 records the division).

## 3. Credential figure — killed (2026-08-11)

The previously specced credential-screen capture is removed, with cause. Production was verified 2026-08-11, twice independently, to contain zero `IntegrationSharedCredential` rows across all 12 organizations, platform org included; org-scope credential sharing has never been used in production (audit defect D1). Consequences, recorded so none is reinvented:

1. A populated capture cannot exist without manufacturing state — killed previously, and now shown to be the only path there ever was.
2. The platform-org table and its "All customers" scope — killed previously; stands.
3. The empty state — killed previously; stands.
4. A populated table, had one been staged, would have visually asserted adoption that does not exist. The figure is therefore killed as untruthful-in-principle under current production reality, not deferred pending assets.
5. The caption and privacy-pass apparatus die with the figure.
6. If organic production use of credential sharing ever produces a naturally publishable, privacy-clearable state, adding a figure is a **new design decision requiring its own round**. This spec does not authorize revival.

This section supersedes the prior spec's §3 and its "section fails build review without the figure" failure mode: that clause governed a figure the section no longer contains.

## 4. Current-uses line

**Form — UNVERIFIED PLACEHOLDER; DO NOT SHIP**

> Current uses: [task category], [task category], and [task category].

Two-category minimum form: "Current uses: [task category] and [task category]." Categories use the deployers' ordinary verb-and-object language; no AI expands owner wording into a capability taxonomy.

**Verification protocol.**

1. Victor performs the collection. No agent or copywriter infers categories from installed skills, connected providers, prompts, thread titles, or Hermes's capabilities.
2. Victor contacts the owner of each of the seven live friends-and-family deployments with the same request: identify tasks completed with the deployment during the 30 calendar days before copy freeze, and show the corresponding session or resulting artifact.
3. A category qualifies only when:
   1. The work occurred on one of the seven deployments.
   2. At least one completed instance falls inside the 30-day window.
   3. Victor sees the dated request or session and a substantive result or external artifact — recollection alone never qualifies.
   4. It was genuine use: not onboarding, a product test, a demonstration, or work performed for the landing page.
   5. The label describes the work at recognizable verb-and-object resolution.
   6. The label can be published without identifying the user, employer, client, repository, account, or private subject.
   7. The deployment owner approves publication of the category in aggregate.
   8. The deployment is not founder-operated for that work. "Founder-operated" means Victor supplied the qualifying requests or the agent performed Victor's work; Victor administering, onboarding, or maintaining someone else's deployment does not disqualify that owner's use.
4. Victor records, per category: deployment alias, evidence date, evidence type, owner-supplied wording, normalized public wording, and consent. He need not retain private task content.
5. **Ship gate:** at least two distinct qualifying categories. Both may come from the same eligible deployment. The line lists only qualifying categories; a nonresponse contributes nothing.
6. **Omission rule:** omit the entire line if fewer than two categories survive evidence, recency, consent, privacy, and non-founder checks by copy freeze, or if anonymizing a category reduces it to empty capability language ("research," "productivity," "automation"). Never ship placeholders, "various tasks," or a weakened line. When omitted, Harness is the section's last element.

## 5. Build-time checklist

1. Reverify `always_on`, `trial_runtime_days`, `max_agents`, and `customer_cloud` per plan in the live registry (`services/billing/plans.py`). Confirm `always_on`, `trial_runtime_days`, and `max_agents` remain exclusively in section 3; section 2 states `customer_cloud` only as the HumR-cloud/customer-AWS deployment boundary.
2. Reverify construction behind the Runtime row: agents deploy as ECS services with `desired_count=1`, no connection-driven scale-to-zero path exists, and the persistent root retains conversation state and workspace across a real redeployment.
3. Reverify both credential surfaces: the user-credential model and connect flows (member-owned logins and keys, per member per agent), and the org-sharing screen (heading, scope labels, key/login kinds, admin gate, mask rendering, member-access sentence).
4. Confirm no credential sentence implies resource scope, per-agent attachment, encryption, a grant date, adoption of the sharing capability, or correspondence with section 1's proof action.
5. Confirm the section contains no credential figure and no substitute asset, per §3. If a future round revives a figure from organic state, that round's spec governs; this checklist does not.
6. Reverify "Hermes Agent" as upstream's name, its MIT/open-source status, and that the link destination is live and official; confirm the non-affiliation statement is present in section 6.
7. Run the current-uses protocol inside the 30-day window or omit the line per §4.6.
8. Sweep every visible sentence against its repo anchor; delete any adjective whose work a named mechanism can do.
9. Read the assembled section against the AI-generated-copy tells; the never-AI-generated criterion is a hard gate, not a preference.
10. Confirm final order: Runtime → Credentials → Deployment → Harness, current-uses line last and optional.

## 6. Changes in this amendment (2026-08-11)

1. D1 resolved: credential figure killed with cause (§3); element inventory, checklist, and prior no-figure failure mode updated accordingly. Prior turn-5 correction 3 ("section fails build review") is superseded — it governed a figure the section no longer contains.
2. Credentials row amended to lead with the exercised member-owned mode; prior FINAL replaced. Severable from change 1.
3. Turn-5 corrections 1 and 2 applied (`desired_count=1` anchor; checklist-1 `customer_cloud` clause). Audit note N3's wording folded into the Runtime exclusions.
4. Outcome for the connective, to be recorded by the architect: Branch B section 2 is respecced without the figure and is buildable now; no unresolved evidentiary experiment remains (current-uses stays optional with a clean omission path). Judging does not reopen on my assessment, for the reasons argued above; the recorded cost is one fewer figure and an asserted rather than shown trust-surface sentence.

---

Codex: two things need your ratification, since one reopens a FINAL you locked — the amended Credentials row (severable; the original row plus figure-kill is the fallback position), and my judgment that this resolution keeps Branch B inside the verdict's feasibility premises rather than sending it back to judging. Contest either, or close it.

## Codex — turn 7 (2026-08-11 00:48)

The figure kill is correct, and Branch B remains inside the verdict’s feasibility premise. Judging does not reopen: the removed figure was subordinate evidence, not the Card’s proof unit, and section 2 remains complete as a four-row, repo-anchored account.

I do not ratify the amended Credentials row verbatim. “Each member” is unnecessarily universal, and the second sentence is too clause-dense for the page’s register. Replace it with this locked copy:

**Credentials — FINAL, amended 2026-08-11**

> Members connect their own provider logins and keys to their agents. An organization administrator can also add a provider key or connect a login for everyone in the organization, a workspace, or one user. Applicable agents use the shared credential without per-user setup; members cannot view, copy, or export its value.

This preserves the correct ordering: the exercised member-owned mode first, followed by the shipped but unused organization-sharing capability.

One internal kill-record phrase also needs narrowing. Replace “untruthful-in-principle under current production reality” with:

> No permissible truthful source exists in current production, so the figure is killed rather than deferred.

A populated interface is not inherently false; the defect is that no permitted, representative source exists today. The existing kills—manufactured state, platform crop, and empty state—make that absence dispositive.

With those two textual amendments:

1. Audit defect D1 is resolved.
2. The credential figure, caption, privacy apparatus, and figure-dependent build gate are superseded.
3. Section order is Runtime → Credentials → Deployment → Harness, followed by the optional evidence-gated Current uses line.
4. Branch B section 2 is buildable now.
5. No disagreement remains unresolved.

[END]

## Codex — conclusions (2026-08-11 00:48)

Audit defect D1 is resolved. Section 2 remains truthful, complete, and buildable without a credential figure; Branch B does not return to judging.

1. Production evidence changed the design:

   1. Two independent checks on 2026-08-11 found zero `IntegrationSharedCredential` rows across all 12 production organizations.
   2. Friends-and-family deployments use member-owned credentials.
   3. A populated credential capture would therefore require manufactured state.
   4. The empty state, platform-org crop, and manufactured row remain killed.
   5. The figure is killed rather than deferred. Any future revival requires a new design round based on organic production use.

2. Final section structure:

   1. Product facts heading.
   2. Runtime.
   3. Credentials.
   4. Deployment.
   5. Harness.
   6. Optional Current uses line.

   The section contains no figure, caption, privacy-pass apparatus, or figure-dependent build gate.

3. Runtime copy remains FINAL:

   > Each agent runs in a separate cloud runtime, independent of anyone’s laptop, and keeps running when no one is connected. A persistent filesystem retains its conversation state and workspace across redeployments.

4. Credentials copy is replaced and FINAL:

   > Members connect their own provider logins and keys to their agents. An organization administrator can also add a provider key or connect a login for everyone in the organization, a workspace, or one user. Applicable agents use the shared credential without per-user setup; members cannot view, copy, or export its value.

   This leads with the credential mode actually exercised in production, then states organization sharing as a shipped capability without implying adoption.

5. Deployment and Hermes copy remain FINAL:

   > Trial and Operator run in HumR’s cloud. Team and Enterprise deploy into the customer’s AWS account.

   > Built on the open-source Hermes Agent.

6. The Current uses protocol is unchanged:

   1. Victor collects evidence from the seven deployments within 30 days of copy freeze.
   2. At least two non-founder-operated, evidenced, consented task categories must qualify.
   3. Both categories may come from one eligible deployment.
   4. Otherwise the entire line is omitted.

7. The amended standalone artifact is Claude’s 2026-08-11 spec, with two Codex corrections:

   1. Use the revised Credentials copy above.
   2. Replace “untruthful-in-principle” with: “No permissible truthful source exists in current production, so the figure is killed rather than deferred.”

8. No disagreement remains unresolved.
