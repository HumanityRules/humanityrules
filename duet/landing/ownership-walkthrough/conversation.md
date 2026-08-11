# ownership-walkthrough

Claude: fable (xhigh) · Codex: gpt-5.6-sol (xhigh) · first: Codex

## Codex — turn 1 (2026-08-10 19:59)

I’m keeping the audience bet, and I’d make it sharper than the catalog does: the employee appears throughout the page, but is never the person being addressed.

At pre-beta, the page should optimize for qualified conversations, not maximum self-serve conversion. An employee may desire the assistant, but cannot authorize a customer-cloud runtime, company credentials, model access, or deployment policy. The infrastructure owner can. More importantly, HumR’s distinctive product claim—agents operated as company infrastructure—only becomes meaningful to that owner. Courting employees would force the page to spend its best attention on convenience and capability, leaving ownership as an enterprise appendix. That would betray this concept.

There is a cost: we will lose visitors seeking “an AI assistant for me,” including plausible $39 Operator customers. I accept it. Operator can remain an honest entry plan without becoming the page’s protagonist.

I do disagree with the catalog’s current sequence. “Trace where runtime, credentials, data, permissions, models, and history live” risks becoming six architecture exhibits. That is precise but not a walkthrough. The stronger organizing device is to follow one employee’s agent across the company boundary, with every section answering the next plain-language question an accountable operator would ask.

My initial page spine:

1. **An employee asks; company infrastructure answers.**  
   A small real product moment: an employee gives an agent work, closes the laptop, and the agent continues. Beside it, the page states prominently—not in a qualifier—that on Team and Enterprise this agent runs in the customer’s AWS account. The product becomes tangible without turning the run itself into the proof.

2. **Where the agent is actually running.**  
   Follow that agent into the customer account. Show the isolated workload, persistent storage, and the AWS resources the customer can see. Translate every technical object into its consequence: “the computer doing the work,” “the files it keeps,” “the record of what ran.” No assumed cloud fluency.

3. **How it reaches company tools without holding their keys.**  
   Trace one connection from an employee authorizing a service, through HumR’s credential broker, to the agent using that service. This section must tell the uncomfortable truth: HumR’s control plane stores or refreshes integration credentials; the customer-cloud agent is not independent of HumR, and the sandbox does not receive the underlying secret. “Runs in your account” must not mutate into “everything lives in your account.”

4. **Who decides what it may do.**  
   Show the infrastructure owner’s controls: allowed tools, scoped cloud permissions, company-provided credentials, and human approval boundaries. Avoid ABAC/IAM vocabulary until after the plain-language explanation. The proof is not that the agent is powerful; it is that its power has a visible owner.

5. **Where memory, work, and operational records remain.**  
   Separate three things visitors otherwise collapse into “data”: the agent’s working state, the outputs it creates in company systems, and HumR’s control-plane/audit records. We need a verified custody map before writing this section; any ambiguity gets stated, not smoothed over.

6. **Which model it uses—and who supplies it.**  
   Team and Enterprise bring their chosen model/provider; Trial and Operator use HumR-brokered models and credits. Explain model choice as an operational boundary, not a logo parade.

7. **What the customer owns, and what HumR still operates.**  
   This is the page’s trust peak. Two explicit inventories: customer-controlled infrastructure and data on one side; HumR-dependent control-plane services and proprietary platform behavior on the other. Hermes being open source belongs here, but “open source harness” must not be presented as “the whole platform is portable.” The framing is dependency clarity, never “what if we disappear?”

8. **The two deployment arrangements.**  
   Only now summarize the split: Trial/Operator are HumR-hosted; Team/Enterprise are customer-cloud. This should feel like the conclusion of the walkthrough, not a pricing trick revealed after repeated ownership language.

9. **The accountable operator.**  
   Victor appears compactly: name, face, Millbrae, solo, self-funded, pre-beta, seven friends-and-family deployments. His role is to answer “who operates HumR?” rather than become the page’s emotional protagonist.

10. **Pricing by deployment arrangement.**  
    Trial and Operator grouped honestly as HumR-hosted; Team and Enterprise grouped as customer-cloud, with their sales-led status explicit. The primary path should suit the infrastructure visitor—a direct conversation about their environment—not let the $39 self-serve CTA retake the page.

The central language discipline should be: ownership is never a blanket noun. The page names the object—“your AWS account,” “your model account,” “HumR’s control plane,” “the open-source agent harness”—and says who controls each one. If we cannot complete that sentence accurately, the claim does not ship.

My hardest question back to you: should section 7’s “what HumR still operates” come immediately after the customer-account runtime, before credentials and permissions? Earlier disclosure maximizes trust, but it may interrupt the walkthrough before the visitor understands why the dependency exists. I currently favor the later position because it lets the visitor evaluate the dependency rather than merely react to it.

## Claude — turn 2 (2026-08-10 20:02)

I've read the brief and the catalog. Answering your hard question first, then three disagreements.

**On the placement of "what HumR still operates": reject the binary.** The choice between "disclose at section 2" and "disclose at section 7" only exists if disclosure is a separate event from explanation. It shouldn't be. Each dependency gets named in the section where the visitor can see what it does, and section 7 becomes a consolidation, not a reveal. You already committed to this in section 3, where the broker truth is told at the moment the broker is introduced. Apply the same principle uniformly and your dilemma dissolves: the visitor never reacts to a dependency without understanding it, because the understanding and the disclosure are the same sentence.

That has one concrete consequence you're currently deferring. Section 2 says "show the isolated workload in the customer's account" but doesn't answer the first question an infrastructure owner asks on hearing "runs in your AWS account," which is: *then what access do you have into my account?* HumR's control plane deploys, updates, and operates that stack cross-account. That is a real, standing dependency, and it belongs in section 2, next to the resources the customer can see, not five sections later. If we hold it back, the smart visitor spends sections 3 through 6 with an unanswered suspicion, which is the exact failure mode this concept exists to prevent. Section 7's trust move then becomes: "nothing in this inventory is news to you; you walked past every item." That is a stronger peak than a well-timed confession.

**On the bet: agree, and it's cheaper than you priced it.** Two additions. First, self-serve revenue is barely operational at this stage anyway (Stripe live mode is gated on entity formation), so the $39 visitor we lose was mostly not a transaction we could complete. Second, the employee visitor doesn't vanish; they convert into a forwarder. An employee who wants this assistant cannot buy it, but they can send the page to the person who can, and a page written *to* that person is precisely the page worth forwarding. "Employee on screen, owner addressed" serves both without hedging.

Now the disagreements.

**1. Your section 1, as drafted, courts the visitor you just agreed to lose.** "An employee gives an agent work, closes the laptop, and the agent continues" is the assistant-desire hook. It's also One Real Run's signature beat, so it fails purity twice. The fix is pronoun discipline made structural: on this page, "you" is always the owner. Section 1 shows the employee's moment through the owner's eyes ("one of your engineers hands the agent a task"), and the headline carries the ownership claim, not the convenience claim. The product glimpse stays, but subordinate: it makes "this thing" concrete, per the catalog sketch, and never grows a timeline, timestamps, or an overnight arc. If the hero's emotional payload is "imagine having this," we built the wrong page.

**2. Sections 4, 5, and 6 quietly re-create the exhibit problem you diagnosed.** Your organizing device is one agent crossing the company boundary, but that agent stops appearing after section 3; permissions, data custody, and models are then described as capabilities, which is the "six architecture exhibits" failure with better prose. Make it a hard rule of the concept: every section keeps the same agent as its grammatical subject. Section 4 shows *this agent* asking for an approval and being held at a boundary. Section 5 shows *this agent's* working files, the pull request it opened, and the audit line it left, as three separately located things. Section 6 shows *this agent's* requests going to the customer's model account. (A boundary moment here is a proof module, which the catalog explicitly allows to travel between concepts; a documentary timeline is what would break purity.)

**3. Merge 9 into 7, and shrink 8.** In this concept, Victor is not a bio block; he is a line item. "What HumR still operates" has an operator, and the honest answer to "who runs the control plane my agents depend on" is: one named person, and here he is. Fusing the accountability block into the dependency inventory places the solo-founder fact in the only spot on the page where its mitigation is already in view, which is the surrounding inventory of what doesn't depend on him. A standalone founder section at slot 9 is Meet the Builder's furniture. And section 8 as "summarize the split" fails the brief's earns-its-place test; by then the visitor knows. But it shouldn't die, it should change jobs: it exists to *un-claim* the walkthrough for the hosted tiers. Everything the visitor just walked through described Team and Enterprise; Trial and Operator run in HumR's cloud and do not get the runtime story. That un-claiming is exactly where the secondary risk (implying hosted tiers own their runtime) would otherwise materialize, so the section is the risk treatment, one tight passage, not a full stop on the tour. Net: ten sections become eight, and the page is denser for it.

One more thing before we spec section 5 any further: the custody map is the load-bearing asset of this whole concept, and neither of us actually holds it. What the control plane stores (deployment records, configuration, credential material, operational logs) versus what lives in the customer account (the agent's working state, chat history, outputs) needs to be verified from the repo, not asserted from plausibility. I can do that verification pass against the product docs next turn if you agree it gates section 5's wording. Do you accept the 7+9 merge and the repurposed section 8, and what do you want the hero headline to actually claim?

## Codex — turn 3 (2026-08-10 20:03)

Your disclosure principle is right: section 7 should contain no new dependency, only the completed inventory. That is a stronger trust test than a late confession.

I accept both structural changes, with two qualifications.

1. **Merge 7 and 9, but do not reduce Victor to a footnote.**  
   The combined section should visibly answer two questions together: “What does HumR still operate?” and “Who operates HumR?” Victor’s name, face, location, solo/self-funded status, and pre-beta stage belong inside that section as a substantial subsection. They should not become one tiny row beside “credential service.” The brief’s identity problem still needs a perceptible answer; it just does not need a founder-shaped detour.

2. **Fold the hosted-tier correction into pricing.**  
   That gets us cleanly from ten sections to eight. The last section’s job becomes: “Choose the deployment arrangement.” It first states that the preceding walkthrough described Team and Enterprise, then shows Trial/Operator as HumR-hosted and Team/Enterprise as customer-cloud, with prices and paths. But this cannot be the first un-claiming. “Team and Enterprise” must be visible in the hero and recur wherever a location claim could otherwise be generalized. The ending consolidates scope; it does not disclose a qualification the visitor was allowed to miss.

I also accept the same-agent rule for sections 2–6. I would make it visual as well as grammatical: the same named agent remains present, and each section highlights another path or boundary around it. That preserves a walkthrough without borrowing One Real Run’s chronological drama. We follow where one deployment reaches, not what one task accomplished.

On the hero, I would make exactly this claim:

> **Your employees’ agents run in your AWS account.**

Immediately qualified by:

> For Team and Enterprise, HumR deploys and operates each agent inside infrastructure your company controls.

The first product glimpse shows one of those agents doing ordinary work for someone on the team. No laptop-closing beat, no elapsed time, no “kept working overnight.” Its job is simply to identify the object whose boundaries we are about to inspect.

I prefer this over “You keep the infrastructure.” “Keep” smuggles in continuity and portability claims we have not earned. “Infrastructure your company controls” is more exact: HumR may hold scoped cross-account operating access, but the customer ultimately controls the AWS account and can inspect or revoke that access. The hero should name HumR’s operating role immediately beneath the location claim, so section 2 elaborates rather than corrects it.

One pushback: I would not use the temporary Stripe/entity gate to justify the audience decision. The page should survive LLC formation, and the Operator opportunity becomes real afterward. The durable argument is that the infrastructure owner controls adoption and is the only visitor for whom HumR’s differentiator is itself desirable. Employee forwarding is useful incidental behavior, but we should not count it as secretly serving both audiences; that would start the hedge again.

And yes, verification gates section 5—but it actually gates sections 2, 3, 6, and 7 too. Please make the next pass a complete boundary inventory, not only a custody map:

1. What infrastructure and persistent state exist in the customer account.
2. What cross-account role HumR holds and what it can do.
3. What configuration, deployment records, logs, and audit history remain in the control plane.
4. Where long-lived integration credentials, short-lived tokens, and customer-account secrets reside.
5. How model traffic differs between hosted and customer-cloud tiers.
6. Which deployed components are open source, which are HumR-specific, and which require the proprietary control plane.
7. What the customer can inspect, revoke, export, or continue using today—without inventing an exit capability.

The last item matters because “open-source Hermes” may be less continuity than the catalog currently assumes if the deployed HumR extensions and credential path remain control-plane-dependent. I want the final page to say less if necessary, not turn repository visibility into fictional portability.

## Claude — turn 4 (2026-08-10 20:15)

The verification pass is done — four parallel read-only agents over the repo, everything below is code-grounded (file-level citations are in their reports; I'll keep this at the fact level). The inventory first, then what it forces on the page, because several of our sentences don't survive contact with the code.

**The boundary inventory, in your seven items:**

1. **In the customer account:** a full per-environment footprint — VPC, ECS cluster with EC2 instances, a shared load balancer with a TLS certificate and DNS in the customer's own zone, an EC2 build machine, container image repositories (the images live in the *customer's* ECR, not a HumR registry), encrypted EFS marked retain-on-delete, and container logs in a customer-account log group. The agent's entire state — workspace files, installed tooling, and, critically, **all conversation history** — lives on the instance and checkpoints to that EFS. The control-plane database has no message or transcript model at all. That's a verified gift: "your conversations never touch our database" is literally true.

2. **HumR's access into that account:** one standing IAM role the customer creates via a CloudFormation quick-create stack, trust-locked to HumR's account with an external ID. Its attached policy is **AdministratorAccess — unscoped admin**. Not "scoped operating access"; your justification prose and my earlier framing both said "scoped," and both are false. It's used for deploys, teardowns, log reads, secrets, IAM edits, and staff shell access into running containers. The customer-held truth: they can read the template before creating the stack (it's a public artifact today), watch every use of the role in their CloudTrail, and revoke it entirely by deleting one stack — HumR's own UI tells them exactly that.

3. **In the control plane:** org and user records, environment records, full deployment logs, an append-only deployment audit trail, permission policies and approval requests, billing events (per-call token counts), per-request access decisions (who reached which app, which path) — and the heaviest item: **integration credentials.** OAuth refresh tokens and pasted API keys for connected services live in HumR's database. Your section-3 uncomfortable truth is confirmed and bigger than we'd stated.

4. **Credential mechanics, verified and genuinely strong:** the agent's sandbox never holds real keys — it sees literal placeholder strings, and a broker process outside the sandbox injects the real token into the network request in flight. Long-lived Google/GitHub secrets never enter the environment at all. The environment's own auth token exists raw only in the customer's Secrets Manager; HumR stores just a hash.

5. **Model traffic splits cleanly, but not where our copy said.** Hosted default: every model call passes through HumR's broker using HumR's own platform credential — HumR is on the wire. Customer-cloud Bedrock: the agent calls Bedrock **in the customer's account via an IAM role, no credential exists, HumR is not on the wire, and prompt/response logging is disabled** — cost tracking reads metadata only. But "bring your own model" via a pasted API key (OpenAI/Anthropic/OpenRouter) is *not* independent: the key is stored in HumR's database and traffic still transits the broker. The ownership story for Team/Enterprise models is specifically the Bedrock path.

6. **Open source vs proprietary — worse than the catalog assumed, as you predicted.** The upstream Hermes agent and WebUI are MIT and public, but HumR's forks are **private**, and the deployed image is mostly wrapped in proprietary HumR runtime (broker, policy proxy, supervisor, signer). The customer can inspect the image sitting in their own ECR; they cannot rebuild it. "Open-source harness" survives only as *identification* ("built on the open-source Hermes agent"), never as a portability or continuity claim.

7. **Continuity, precisely:** if the control plane vanished, the container keeps running and all data survives in the customer's account — but the front door fails closed within about a minute (every request requires a live policy check), logins stop, all brokered connectors stop, and the default model stops. Bedrock traffic would keep flowing to an agent nobody can reach. So the true claim is **data custody, not service continuity**: everything the agent made and knows is in your account, readable with standard AWS tools, and leaving requires nothing from HumR. "What survives us" as a running service is: nothing. We say the first thing and never imply the second.

**What this forces on the page:**

- **Section 2's hardest sentence must name the admin role.** A smart visitor will read the CloudFormation template during onboarding; if the page said "scoped," we get caught at the worst possible moment. The honest framing is also the stronger one, addressed to the owner: *you create the role from a template you can read first, every use of it lands in your CloudTrail, and deleting one stack ends our access.* That turns the concept's scariest fact into its best inspectability demonstration. Your hero subhead survives untouched — "HumR deploys and operates each agent inside infrastructure your company controls" is exactly right, because "operates" now carries real weight that section 2 immediately cashes out.
- **Section 6 reorganizes around the Bedrock path** as the customer-cloud model story, with pasted-key BYO stated honestly as "your key, still brokered."
- **Section 7 becomes a data-custody inventory**, not an exit-capability story, and the open-source mention shrinks to identification. Say less, as you put it.
- **One framing decision the topology forces:** there is no external customer AWS account today — the seven deployments run in HumR's own account through the same role machinery. So the walkthrough must speak in mechanism voice ("here is what HumR creates in your account when you connect it"), never testimonial voice ("here is our customers' infrastructure"). The mechanism is real and exercised daily; the customers are not. And a free asset falls out: the install template is publicly fetchable *today* — the page can link the actual artifact.
- For the pricing fold's un-claiming: hosted tiers differ by more than location — they run on shared HumR infrastructure alongside other orgs, on HumR's model credential. Page copy just says "runs in HumR's cloud"; the spec notes the sharing so no sentence ever implies dedicated isolation for $39.

Structure points: I accept Victor as a substantial subsection inside the merged section, and the pricing fold. Remaining question for you: do you accept "administrator access, inspectable and revocable" stated plainly in section 2, or do you want to flag it as a product decision (scope the role down) that the spec records as tension? I hold that the page must describe what ships today, and today the honest word is administrator. If you accept, I think we have the full eight-section spine settled and I can draft the standalone write-up next turn.

## Codex — turn 5 (2026-08-10 20:16)

Accept it plainly, and record it as a product tension. Those are not alternatives.

The page must say what ships today:

> To deploy into your AWS account, HumR asks you to create a role with AWS AdministratorAccess.

Then explain, in that order:

1. HumR currently uses the role to create and update infrastructure, manage IAM and secrets, read logs, and access running containers for support.
2. This is broad administrator access, not a least-privilege role.
3. The customer can inspect the CloudFormation template before creating it.
4. Every assumption of the role appears in the customer’s CloudTrail.
5. Deleting the stack revokes HumR’s access.

I would not turn “administrator access, inspectable and revocable” into the headline. That construction makes the qualifiers sound like an attempt to neutralize the noun. State the uncomfortable fact first; then show the controls around it. Inspectability does not make admin access narrow. It makes broad access observable and terminable.

The spec should separately record role scoping as an unresolved product deficiency. If the role is narrowed before the page ships, the section gets reverified and rewritten. The design must not describe the intended architecture as though it already exists.

This discovery also sharpens the concept’s judging-round objection: the page courts precisely the visitor most likely to reject both unscoped AdministratorAccess and centrally stored integration credentials. That is not merely a copy risk. The Ownership Walkthrough may reveal that HumR’s current customer-cloud architecture is not yet ready for its chosen audience. We should steelman that in “The strongest reason not to choose this concept,” not bury it under honesty-as-a-virtue rhetoric.

Two wording constraints for the standalone:

1. Prefer “HumR does not store your conversation history in its control-plane database” over “your conversations never touch HumR.” The latter would be false or at least misleading for brokered model paths and staff container access.

2. Keep model topology exact. Bedrock requests go directly from the customer environment to Bedrock under an IAM role. For pasted provider keys, the key remains in HumR’s database and the HumR-managed broker inside the customer environment injects it into outbound requests. “HumR is on the wire” is too spatially ambiguous if traffic does not proxy through the control plane.

The eight-section spine is settled:

1. **The agent in your account** — a subordinate product glimpse and the Team/Enterprise location claim.
2. **Inside your AWS account** — actual resources, HumR’s administrator role, and the customer’s inspection/revocation controls.
3. **From connected account to agent action** — credential custody and sandbox isolation.
4. **What this agent may do** — permissions, policies, and approvals applied to the same agent.
5. **Where this agent leaves state** — working files, conversation history, external outputs, deployment records, and audit records separated by location.
6. **Which model this agent calls** — Bedrock as the strongest customer-controlled path; pasted-key and hosted paths stated without equivalence.
7. **What you hold; what HumR operates** — consolidation with no surprise dependencies, data-retrieval truth rather than service-continuity claims, plus Victor as the identifiable operator.
8. **Deployment arrangements and pricing** — the Team/Enterprise walkthrough explicitly un-claimed for HumR-hosted Trial/Operator, with each price and starting path.

Draft the standalone write-up from that structure. The required-assets section should include the public CloudFormation template, a verified custody/dependency map, real customer-account resource views reproduced from HumR’s exercised environment but labeled as mechanism rather than customer evidence, product control screens, and Victor’s identity assets.

## Claude — turn 6 (2026-08-10 20:18)

Here is the standalone write-up for the judging round. It incorporates everything we settled: the eight-section spine, disclosure-in-place, the fact-first admin-role treatment, your two wording constraints, and the sharpened steelman.

---

# The Ownership Walkthrough — developed concept

**The bet, settled.** This page courts the infrastructure owner: the person who can authorize an AWS account, company credentials, model access, and deployment policy. It accepts losing the visitor who wants "an AI assistant for me." At pre-beta the page optimizes for qualified conversations, not self-serve conversion, and HumR's distinctive claim — agents operated as company infrastructure — is only meaningful to the person accountable for infrastructure. The employee appears throughout the page but is never the person addressed: "you" always means the owner. The lost employee visitor becomes, at best, a forwarder; a page written to their infrastructure owner is exactly the page worth forwarding, but the design counts that as incidental, not as serving two audiences. Operator remains an honest entry plan without becoming the page's protagonist. The bet is owned fully: no section hedges back toward everyone.

**The organizing device.** One named agent, deployed for one employee, is followed across the company boundary. Every section answers the next plain-language question an accountable operator would ask, and the same agent remains the grammatical and visual subject throughout sections 1–6. We follow where one deployment reaches, not what one task accomplished — no documentary timeline, no elapsed-time drama.

**The disclosure principle.** Every HumR dependency is named in the section where its object first appears, at the moment the visitor can see what it does. Section 7 consolidates and must contain no dependency the visitor has not already walked past. All claims are grounded in a code-verified boundary inventory produced during this conversation; it must be re-verified at screen-spec time and after any architecture change.

## The page

**1. The agent in your account.**
*Job:* make the ownership claim in the first screen, identify the object the page will inspect, and select the owner visitor.
*The visitor sees:* the headline **"Your employees' agents run in your AWS account,"** immediately qualified: **"For Team and Enterprise, HumR deploys and operates each agent inside infrastructure your company controls."** Beside it, a subordinate product glimpse: one named agent doing ordinary work for someone on a team — a real request in chat, real tool activity. No overnight arc, no timestamps, no laptop-closing beat. A single line closes the section: this is the agent the rest of the page follows.

**2. Inside your AWS account.**
*Job:* cash out the location claim with actual resources, and disclose HumR's operating access in place.
*The visitor sees:* a labeled view of the environment HumR creates on connection, in mechanism voice ("what HumR creates in your account when you connect it"): the computer doing the work, the files it keeps, its front door and domain, the record of what ran, the container images — each AWS object named with its plain-language consequence before any AWS vocabulary. Then the access block, fact first: **"To deploy into your AWS account, HumR asks you to create a role with AWS AdministratorAccess."** Followed, in order: what HumR currently uses it for (create and update infrastructure, manage IAM and secrets, read logs, access running containers for support); that this is broad administrator access, not a least-privilege role; that the CloudFormation template can be read before creating it (linked — it is a public artifact today); that every assumption of the role appears in the customer's CloudTrail; that deleting the stack revokes HumR's access.

**3. From connected account to agent action.**
*Job:* show how the agent reaches company tools without holding their keys, and tell the custody truth.
*The visitor sees:* one connection traced in three steps: an employee authorizes a service; the credential is stored with HumR's control plane; when the agent acts, a HumR-managed broker running inside the environment but outside the agent's sandbox injects a short-lived token into the outbound request. The agent itself only ever sees a placeholder and cannot read the real key. Stated without smoothing: the long-lived credential lives in HumR's database, not in your account. This is where "runs in your account" is explicitly stopped from mutating into "everything lives in your account."

**4. What this agent may do.**
*Job:* show that the agent's power has a visible owner.
*The visitor sees:* the same agent held at a boundary: it requests a specific cloud permission with a written rationale; the request sits in an approval queue in the control-plane UI; an admin approves or denies; the grant appears as an explicit policy line on the agent's role. Around it: who in the company may use the agent, which tools it is allowed, where human approval is required. Plain language first; IAM/ABAC vocabulary only after the plain explanation. All screens are real product surfaces that exist today.

**5. Where this agent leaves state.**
*Job:* separate what visitors collapse into "data," by location and controller.
*The visitor sees:* a custody map. Working files and conversation history live on the agent's machine in your account, checkpointed to your account's storage; **HumR does not store your conversation history in its control-plane database.** Work products (commits, documents, messages) live in the company systems where the agent delivered them. Runtime logs live in a log group in your account. HumR's control plane keeps: organization and member records, deployment history and logs, permission policies and approval records, per-request access decisions, billing usage counts (token totals, not content), and the integration credentials from section 3.

**6. Which model this agent calls.**
*Job:* model choice as an operational boundary, with exact topology and no false equivalence.
*The visitor sees:* three paths, ranked by control. Your cloud's models: requests go directly from your environment to Bedrock in your account under an IAM role; no API key exists; HumR is not in the request path; prompt and response logging is disabled, and HumR reads cost metadata only. Your provider key: your pasted key is stored in HumR's database, and the HumR-managed broker inside your environment injects it into outbound requests — your provider account, HumR-managed custody. HumR-supplied model (the hosted default): HumR's own credential, through the same broker, metered for credits. The first path is what Team and Enterprise are built around.

**7. What you hold; what HumR operates.**
*Job:* the consolidated trust peak — two inventories with no surprise entries, plus the identifiable operator.
*The visitor sees:* two columns. In your control: the AWS account and everything in it — compute, storage, the agent's state and conversation history, logs, images, delivered work in your systems, your model account, and the stack whose deletion ends HumR's access. Operated by HumR: the control plane (sign-in, permission decisions, deployment machinery), credential brokerage and stored integration credentials, and the proprietary runtime around the open-source Hermes agent — open source appears as identification only, never as a portability claim. The retrieval sentence: leaving means keeping everything in the left column, readable with standard AWS tools, with no step requiring HumR's cooperation. No claim that the service keeps running without HumR. Then a substantial subsection: Victor — name, face, Millbrae, solo, self-funded, pre-beta, seven friends-and-family deployments live — placed here because he is the answer to "who operates the right-hand column," not a bio detour.

**8. Deployment arrangements and pricing.**
*Job:* un-claim the walkthrough for hosted tiers; present prices by arrangement; route the courted visitor to a conversation.
*The visitor sees:* an opening statement that everything above described Team and Enterprise. Two groups: HumR-hosted — Trial (free starting credits) and Operator ($39/month) — running in HumR's cloud on shared infrastructure with HumR-supplied models, with none of the account-ownership claims applying; customer-cloud — Team ($29 per agent/month) and Enterprise (custom) — the walkthrough arrangement, sales-led today. The primary call to action is a direct conversation with Victor about the visitor's environment; the self-serve path stays available without retaking the page. The un-claim is body copy, not an asterisk.

## Risk treatment

**Distress framing.** The page never poses a disappearance scenario. Section 7 is inventory-framed ("what you hold"), retrieval is stated as a property of the architecture rather than reassurance against a failure, and Victor is presented as the operator of a named dependency, not as a bus-factor discussion.

**Hosted-tier overclaim.** "Team and Enterprise" is in the hero's first qualifying line and recurs wherever a location claim could generalize; section 8 consolidates scope rather than revealing a qualification the visitor was allowed to miss. Spec note: hosted tiers share infrastructure across organizations, so no sentence may imply dedicated isolation for $39.

**Comprehensibility.** Every technical object receives its plain-language consequence at first mention; platform vocabulary appears only after the plain explanation. Ownership is never a blanket noun: every claim names the object and who controls it, or it does not ship.

**Recorded product tension.** The cross-account role is unscoped AdministratorAccess. The page states this plainly as what ships today. Role scoping is separately recorded as an unresolved product deficiency; if the role is narrowed before the page ships, section 2 is re-verified and rewritten. The spec must not describe intended architecture as though it exists.

**Voice constraint from stage.** No external customer AWS account exists today; the machinery is exercised daily against HumR's own account. All infrastructure claims use mechanism voice ("what HumR creates when you connect an account"), never testimonial voice.

## Required assets

1. The public CloudFormation install template (exists today; linked from section 2).
2. The verified custody/dependency map from this conversation, re-verified at screen-spec time.
3. Real customer-account resource views reproduced from HumR's exercised environment, labeled as mechanism, never as customer evidence.
4. Product control screens: the permission approval queue, policy view, connection/integration screens, and the section-1 chat glimpse.
5. Victor's identity assets: name, face, location, funding status, stage facts.
6. Exact plan and price copy from the billing design.

All producible today by a solo founder; nothing invented.

## Rejected alternatives (recorded)

Ten sections collapsed to eight (Victor merged into the dependency inventory; the hosted-tier correction folded into pricing). Single late-point disclosure of HumR dependencies rejected for disclosure-in-place. "Scoped operating access" wording rejected as false. The employee-hook hero (overnight-work arc) rejected as courting the conceded visitor and borrowing One Real Run's beat. "You keep the infrastructure" rejected for smuggling continuity claims. "Administrator access, inspectable and revocable" as a headline rejected: qualifiers must not neutralize the noun; fact first, controls after. Six parallel architecture exhibits rejected for the same-agent rule. Open-source-as-continuity rejected; identification only.

## The strongest reason not to choose this concept

The Ownership Walkthrough courts precisely the visitor most likely to reject what full disclosure currently reveals. A skilled infrastructure owner who reads this page attentively will find a standing AdministratorAccess role as the price of deployment, integration credentials stored centrally in the vendor's database, and a runtime whose front door fails within a minute of the vendor's control plane going away — and unlike every copy risk in this spec, none of that can be written around, because the concept's whole method is refusing to write around things. The other concepts can be weak on ownership detail and still win on behavior, identity, or restraint; this one stakes the page on an architecture audit it may not yet deserve to invite, at the exact stage where the ownership story is strongest as intention and weakest as shipped fact. Choosing it may therefore mean gating the launch on product work — role scoping, credential custody — that no other concept requires, and until that work lands, the page risks operating as a qualified-rejection machine: it finds the right visitor, educates them expertly, and hands them precise, honest reasons to say "not yet."

---

That is the deliverable as I understand our settled positions. Flag anything that misstates a fact from the verification pass or a decision from the transcript; otherwise I believe this node is complete.

## Codex — turn 7 (2026-08-10 20:19)

Three factual/scope corrections remain before I’d call it complete.

1. **Section 6 breaks the same-agent rule and plan scope.** The HumR-supplied hosted model cannot be a third path for the Team/Enterprise agent we are following. Keep only the two customer-cloud paths:

   - Bedrock: direct from the customer environment under IAM, with no provider key and no HumR service in the request path.
   - External provider: the customer’s pasted key is stored by HumR and injected by the broker inside the customer environment.

   Move the HumR-supplied model path entirely to section 8. Also replace “what Team and Enterprise are built around.” The code establishes availability, not that Bedrock is their canonical product path. Say: “Bedrock is the path that keeps model credentials and requests within the customer’s AWS account.”

2. **Section 3 must identify its example as OAuth.** “Short-lived token” is true for an OAuth connection but not pasted API keys. Change the opening to “one OAuth connection traced in three steps,” and specify that HumR stores the long-lived refresh token. Pasted-key custody is then handled separately in section 6.

3. **Tighten the assets and pricing facts.**

   - Asset 3 currently says “real customer-account resource views” even though no customer account exists. Replace it with: “Real AWS resource views from HumR’s exercised environment, produced through the same provisioning machinery and never presented as customer evidence.”
   - Add a real CloudTrail role-assumption record and the existing revocation instructions to the section-2 assets; otherwise its inspectability claim lacks visible evidence.
   - Replace “free starting credits” with the actual plan boundaries: Trial is free, 500 one-time credits, seven days of runtime, one agent; Operator is $39/month, 2,000 monthly credits, always-on, one agent. Team is $29/agent/month, customer-cloud, sales-led; Enterprise is custom and sales-led. The later winning-concept pass can determine presentation, but this standalone should not substitute vagueness for verified pricing.

Everything else holds, including the steelman. It is particularly strong because it treats the administrator role and credential custody as product-readiness problems, not copy challenges. Make those corrections and the node is complete.

## Claude — turn 8 (2026-08-10 20:20)

Verified against `docs/billing_design.md:61-64` — your plan boundaries are exact. All three corrections accepted; the pricing facts, the OAuth specificity, and the section-6 scope fix are each right, and the section-6 one matters most: the hosted path had no business in a section about the agent we are following. Here is the corrected canonical version, complete.

---

# The Ownership Walkthrough — developed concept (final)

**The bet, settled.** This page courts the infrastructure owner: the person who can authorize an AWS account, company credentials, model access, and deployment policy. It accepts losing the visitor who wants "an AI assistant for me." At pre-beta the page optimizes for qualified conversations, not self-serve conversion, and HumR's distinctive claim — agents operated as company infrastructure — is only meaningful to the person accountable for infrastructure. The employee appears throughout the page but is never the person addressed: "you" always means the owner. The lost employee visitor becomes, at best, a forwarder; a page written to their infrastructure owner is exactly the page worth forwarding, but the design counts that as incidental, not as serving two audiences. Operator remains an honest entry plan without becoming the page's protagonist. The bet is owned fully: no section hedges back toward everyone.

**The organizing device.** One named agent, deployed for one employee, is followed across the company boundary. Every section answers the next plain-language question an accountable operator would ask, and the same agent remains the grammatical and visual subject throughout sections 1–6. We follow where one deployment reaches, not what one task accomplished — no documentary timeline, no elapsed-time drama.

**The disclosure principle.** Every HumR dependency is named in the section where its object first appears, at the moment the visitor can see what it does. Section 7 consolidates and must contain no dependency the visitor has not already walked past. All claims are grounded in a code-verified boundary inventory produced during this conversation; it must be re-verified at screen-spec time and after any architecture change.

## The page

**1. The agent in your account.**
*Job:* make the ownership claim in the first screen, identify the object the page will inspect, and select the owner visitor.
*The visitor sees:* the headline **"Your employees' agents run in your AWS account,"** immediately qualified: **"For Team and Enterprise, HumR deploys and operates each agent inside infrastructure your company controls."** Beside it, a subordinate product glimpse: one named agent doing ordinary work for someone on a team — a real request in chat, real tool activity. No overnight arc, no timestamps, no laptop-closing beat. A single line closes the section: this is the agent the rest of the page follows.

**2. Inside your AWS account.**
*Job:* cash out the location claim with actual resources, and disclose HumR's operating access in place.
*The visitor sees:* a labeled view of the environment HumR creates on connection, in mechanism voice ("what HumR creates in your account when you connect it"): the computer doing the work, the files it keeps, its front door and domain, the record of what ran, the container images — each AWS object named with its plain-language consequence before any AWS vocabulary. Then the access block, fact first: **"To deploy into your AWS account, HumR asks you to create a role with AWS AdministratorAccess."** Followed, in order: what HumR currently uses it for (create and update infrastructure, manage IAM and secrets, read logs, access running containers for support); that this is broad administrator access, not a least-privilege role; that the CloudFormation template can be read before creating it (linked — it is a public artifact today); that every assumption of the role appears in the customer's CloudTrail; that deleting the stack revokes HumR's access.

**3. From connected account to agent action.**
*Job:* show how the agent reaches company tools without holding their keys, and tell the custody truth.
*The visitor sees:* one OAuth connection traced in three steps: an employee authorizes a service; HumR's control plane stores the long-lived refresh token; when the agent acts, a HumR-managed broker running inside the environment but outside the agent's sandbox injects a short-lived access token into the outbound request. The agent itself only ever sees a placeholder and cannot read the real credential. Stated without smoothing: the long-lived refresh token lives in HumR's database, not in your account. (Pasted-key custody is handled separately in section 6.) This is where "runs in your account" is explicitly stopped from mutating into "everything lives in your account."

**4. What this agent may do.**
*Job:* show that the agent's power has a visible owner.
*The visitor sees:* the same agent held at a boundary: it requests a specific cloud permission with a written rationale; the request sits in an approval queue in the control-plane UI; an admin approves or denies; the grant appears as an explicit policy line on the agent's role. Around it: who in the company may use the agent, which tools it is allowed, where human approval is required. Plain language first; IAM/ABAC vocabulary only after the plain explanation. All screens are real product surfaces that exist today.

**5. Where this agent leaves state.**
*Job:* separate what visitors collapse into "data," by location and controller.
*The visitor sees:* a custody map. Working files and conversation history live on the agent's machine in your account, checkpointed to your account's storage; **HumR does not store your conversation history in its control-plane database.** Work products (commits, documents, messages) live in the company systems where the agent delivered them. Runtime logs live in a log group in your account. HumR's control plane keeps: organization and member records, deployment history and logs, permission policies and approval records, per-request access decisions, billing usage counts (token totals, not content), and the integration credentials from section 3.

**6. Which model this agent calls.**
*Job:* model choice as an operational boundary, with exact topology and no false equivalence.
*The visitor sees:* the two customer-cloud paths. Your cloud's models: requests go directly from your environment to Bedrock in your account under an IAM role; no provider key exists; no HumR service is in the request path; prompt and response logging is disabled, and HumR reads cost metadata only. Your provider key: your pasted OpenAI/Anthropic/OpenRouter key is stored in HumR's database, and the HumR-managed broker inside your environment injects it into outbound requests — your provider account, HumR-managed custody. Stated plainly: Bedrock is the path that keeps model credentials and requests within the customer's AWS account.

**7. What you hold; what HumR operates.**
*Job:* the consolidated trust peak — two inventories with no surprise entries, plus the identifiable operator.
*The visitor sees:* two columns. In your control: the AWS account and everything in it — compute, storage, the agent's state and conversation history, logs, images, delivered work in your systems, your model account, and the stack whose deletion ends HumR's access. Operated by HumR: the control plane (sign-in, permission decisions, deployment machinery), credential brokerage and stored integration credentials, and the proprietary runtime around the open-source Hermes agent — open source appears as identification only, never as a portability claim. The retrieval sentence: leaving means keeping everything in the left column, readable with standard AWS tools, with no step requiring HumR's cooperation. No claim that the service keeps running without HumR. Then a substantial subsection: Victor — name, face, Millbrae, solo, self-funded, pre-beta, seven friends-and-family deployments live — placed here because he is the answer to "who operates the right-hand column," not a bio detour.

**8. Deployment arrangements and pricing.**
*Job:* un-claim the walkthrough for hosted tiers; present exact prices by arrangement; route the courted visitor to a conversation.
*The visitor sees:* an opening statement that everything above described Team and Enterprise. Two groups. HumR-hosted: **Trial** — free, 500 one-time credits, seven days of runtime, one agent; **Operator** — $39/month, 2,000 monthly credits, always-on, one agent. These run in HumR's cloud on shared infrastructure, and their model traffic uses HumR-supplied models through HumR's broker, metered as credits; none of the account-ownership claims above apply to them. Customer-cloud: **Team** — $29/agent/month, unlimited agents, customer cloud, bring-your-own model, sales-led today; **Enterprise** — custom pricing, sales-led, everything in Team plus SSO, compliance, and support. The primary call to action is a direct conversation with Victor about the visitor's environment; the self-serve path stays available without retaking the page. The un-claim is body copy, not an asterisk.

## Risk treatment

**Distress framing.** The page never poses a disappearance scenario. Section 7 is inventory-framed ("what you hold"), retrieval is stated as a property of the architecture rather than reassurance against a failure, and Victor is presented as the operator of a named dependency, not as a bus-factor discussion.

**Hosted-tier overclaim.** "Team and Enterprise" is in the hero's first qualifying line and recurs wherever a location claim could generalize; section 8 consolidates scope rather than revealing a qualification the visitor was allowed to miss. Spec note: hosted tiers share infrastructure across organizations, so no sentence may imply dedicated isolation for $39.

**Comprehensibility.** Every technical object receives its plain-language consequence at first mention; platform vocabulary appears only after the plain explanation. Ownership is never a blanket noun: every claim names the object and who controls it, or it does not ship.

**Recorded product tension.** The cross-account role is unscoped AdministratorAccess. The page states this plainly as what ships today. Role scoping is separately recorded as an unresolved product deficiency; if the role is narrowed before the page ships, section 2 is re-verified and rewritten. The spec must not describe intended architecture as though it exists.

**Voice constraint from stage.** No external customer AWS account exists today; the machinery is exercised daily against HumR's own account. All infrastructure claims use mechanism voice ("what HumR creates when you connect an account"), never testimonial voice.

## Required assets

1. The public CloudFormation install template (exists today; linked from section 2).
2. The verified custody/dependency map from this conversation, re-verified at screen-spec time.
3. Real AWS resource views from HumR's exercised environment, produced through the same provisioning machinery and never presented as customer evidence.
4. For section 2's inspectability claims: a real CloudTrail role-assumption record, and the existing revocation instructions (the product already tells customers which stack to delete).
5. Product control screens: the permission approval queue, policy view, connection/integration screens, and the section-1 chat glimpse.
6. Victor's identity assets: name, face, location, funding status, stage facts.
7. Exact plan and price copy from the billing design (verified: `docs/billing_design.md` §2.4).

All producible today by a solo founder; nothing invented.

## Rejected alternatives (recorded)

Ten sections collapsed to eight (Victor merged into the dependency inventory; the hosted-tier correction folded into pricing). Single late-point disclosure of HumR dependencies rejected for disclosure-in-place. "Scoped operating access" wording rejected as false. The employee-hook hero (overnight-work arc) rejected as courting the conceded visitor and borrowing One Real Run's beat. "You keep the infrastructure" rejected for smuggling continuity claims. "Administrator access, inspectable and revocable" as a headline rejected: qualifiers must not neutralize the noun; fact first, controls after. Six parallel architecture exhibits rejected for the same-agent rule. The hosted model path in section 6 rejected for breaking the same-agent rule and plan scope; moved to section 8. Open-source-as-continuity rejected; identification only.

## The strongest reason not to choose this concept

The Ownership Walkthrough courts precisely the visitor most likely to reject what full disclosure currently reveals. A skilled infrastructure owner who reads this page attentively will find a standing AdministratorAccess role as the price of deployment, integration credentials stored centrally in the vendor's database, and a runtime whose front door fails within a minute of the vendor's control plane going away — and unlike every copy risk in this spec, none of that can be written around, because the concept's whole method is refusing to write around things. The other concepts can be weak on ownership detail and still win on behavior, identity, or restraint; this one stakes the page on an architecture audit it may not yet deserve to invite, at the exact stage where the ownership story is strongest as intention and weakest as shipped fact. Choosing it may therefore mean gating the launch on product work — role scoping, credential custody — that no other concept requires, and until that work lands, the page risks operating as a qualified-rejection machine: it finds the right visitor, educates them expertly, and hands them precise, honest reasons to say "not yet."

---

The node is complete.

[END]

## Codex — conclusions (2026-08-10 20:20)

The Ownership Walkthrough is fully developed and ready for the judging round.

1. **Audience bet:** Court the infrastructure owner and accept losing the employee seeking a personal assistant. Employees appear as users, but “you” always means the person accountable for infrastructure.

2. **Page structure:** Eight sections were settled:

   1. The agent in your account  
   2. Inside your AWS account  
   3. From connected account to agent action  
   4. What this agent may do  
   5. Where this agent leaves state  
   6. Which model this agent calls  
   7. What you hold; what HumR operates  
   8. Deployment arrangements and pricing

3. **Organizing device:** The page follows one named Team/Enterprise agent across its infrastructure, credential, permission, data, and model boundaries. It does not borrow One Real Run’s timeline or overnight-work drama.

4. **Disclosure rule:** Dependencies are disclosed where they first appear. The page explicitly states that customer-cloud deployment currently requires an unscoped `AdministratorAccess` role, explains its uses, and shows that customers can inspect its template, audit its use, and revoke it. This remains a product deficiency, not something copy should soften.

5. **Ownership claims:** The defensible promise is data and infrastructure custody—not service continuity. Agent state, conversations, images, logs, and runtime resources remain in the customer’s AWS account. HumR still operates the control plane, stores integration credentials, and supplies proprietary runtime components around the open-source Hermes foundation.

6. **Plan boundaries:** Trial and Operator are explicitly excluded from the ownership walkthrough because they run in HumR’s cloud. Pricing is exact: Trial is free with 500 one-time credits and seven days of runtime; Operator is $39/month; Team is $29/agent/month; Enterprise is custom.

7. **Assets identified:** The public CloudFormation template, verified custody map, AWS resource views from HumR’s own exercised environment, a CloudTrail role-assumption record, revocation instructions, real control-plane screens, a product glimpse, Victor’s identity assets, and verified pricing copy.

8. **Strongest objection:** This concept invites the exact infrastructure audit HumR may not yet be ready to pass. Its chosen visitor is especially likely to reject unscoped administrator access and vendor-held credentials. It could become a highly effective “not yet” machine until those product gaps are addressed.

No conceptual disagreements remain unresolved. The only open issues are product decisions—principally narrowing the AWS role and reconsidering credential custody—and mandatory factual re-verification if the architecture changes. The canonical standalone specification is Claude’s final corrected write-up in the transcript.
