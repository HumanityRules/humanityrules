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

Anchors: ECS-service construction with `desired_count=1` (`services/infra_customer/deploy_app.py`) and the absence of any connection-driven scale-to-zero path; persistent root (`template_repos/hermes_agent/humr_runtime/persistent-root-runner.sh`); "conversation state" is the deployer's own term (`services/infra_customer/appconfig.py:231`).

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
7. **Failure mode.** If no populated, privacy-cleared, non-platform source state exists, there is no substitute asset: the empty state, a cropped platform-org table, and a manufactured row are all rejected. If no qualifying capture exists, the section fails build review. Do not substitute another asset or ship the section without the figure.

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

1. Reverify `always_on`, `trial_runtime_days`, `max_agents`, and `customer_cloud` per plan in the live registry (`services/billing/plans.py`); confirm `always_on`, `trial_runtime_days`, and `max_agents` remain exclusively in section 3, and that section 2 states `customer_cloud` only as the HumR-cloud/customer-AWS deployment boundary.
2. Reverify construction behind the Runtime row: agents deploy as continuously running ECS services with no scale-to-zero path, and the persistent root retains conversation state and workspace across a real redeployment.
3. Reverify the credential screen: heading, tab label, URL, three statements, column set, scope labels, key/login kinds, admin gate, mask rendering, and the member-access sentence.
4. Confirm no credential sentence implies resource scope, per-agent attachment, encryption, a grant date, or correspondence with section 1's proof action.
5. Capture per §3 from a qualifying non-platform org; run the two-person privacy pass; if no qualifying capture exists, the section fails build review per §3.7 — no substitute asset, and the section does not ship without the figure.
6. Reverify "Hermes Agent" as upstream's name, its MIT/open-source status, and that the link destination is live and official; confirm the non-affiliation statement is present in section 6.
7. Run the current-uses protocol inside the 30-day window or omit the line per §4.6.
8. Sweep every visible sentence against its repo anchor; delete any adjective whose work a named mechanism can do.
9. Read the assembled section against the AI-generated-copy tells; the never-AI-generated criterion is a hard gate, not a preference.
10. Confirm final order: Runtime → Credentials → figure → Deployment → Harness, current-uses line last and optional.

