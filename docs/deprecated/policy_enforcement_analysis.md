# Analysis: Policy Enforcement — Pulumi CrossGuard vs CDK/AWS

> How does Pulumi's policy enforcement work, what are the CDK/AWS equivalents, and what would DOH need to implement a comparable system? Focus: internal apps deployed by non-DevOps engineers (the vibe-coded internal tools use case).

---

## What Pulumi CrossGuard Provides

Pulumi's policy system has three layers:

**Policy** — A single rule that validates a resource. Receives the resource type and properties, returns pass or fail. Can be `advisory` (warn) or `mandatory` (block). Example: "S3 buckets must have encryption enabled."

**Policy Pack** — A collection of policies bundled together. Written in TypeScript or Python. Shipped as versioned packages. Pulumi provides pre-built compliance packs (PCI DSS, SOC 2, CIS benchmarks) as part of their enterprise offering.

**Policy Group** — Associates policy packs with stacks. An admin says "all stacks in the production group must pass the PCI pack." Managed centrally in Pulumi Cloud, not in the infrastructure code. This is the management layer that makes policies enforceable at an organizational level without modifying individual projects.

Policies run at `pulumi preview` time — after the desired state is computed, before any resources are created. This is preventive, not reactive.

---

## CDK and AWS Equivalents

### Client-Side (Before CloudFormation Submission)

**CDK Aspects + cdk-nag** — Aspects traverse the CDK construct tree at synth time. The `cdk-nag` library ships pre-built rule packs: AWS Solutions, HIPAA, NIST 800-53, PCI DSS. Attached with one line: `Aspects.of(app).add(AwsSolutionsChecks())`. Equivalent to Pulumi's Policy + Policy Pack layers, but the Aspect must be added in the CDK code itself — there is no external management layer.

**CloudFormation Guard** — A declarative, domain-specific language for validating CloudFormation templates. AWS publishes sample rule sets. Runs as a CLI tool (`cfn-guard validate`) against the synthesized template. Like cdk-nag, it's client-side only.

### Server-Side (Enforced by AWS)

**CloudFormation Hooks** — Run server-side before CloudFormation creates, updates, or deletes a resource. Registered as CloudFormation extensions. The `AWS::Hooks::GuardHook` type lets you run Guard rules server-side, stored in S3. This is the only truly preventive, bypass-proof mechanism.

**Service Control Policies (SCPs)** — Organization-level guardrails that restrict API calls. Too coarse for resource-property validation (can block "no public S3 buckets" but not "ECS tasks must have at least 512MB memory").

**AWS Config Rules + Conformance Packs** — Evaluate compliance after deployment. Pre-built packs exist for PCI DSS, HIPAA, NIST, CIS. Reactive, not preventive.

### What's Missing Across All AWS Options

None of the CDK or AWS mechanisms provide the **Policy Group** concept — centralized assignment of rule sets to environments or organizational units, managed outside of the infrastructure code. This is the management layer that Pulumi Cloud provides as part of its enterprise offering.

---

## What DOH Needs

### Enforcement Boundary

DOH controls the full deployment pipeline: the agent generates CDK code, DOH synths it, DOH deploys it. There is no untrusted actor who can bypass a client-side check. Server-side enforcement (CloudFormation Hooks) solves the problem of rogue developers skipping the CLI, which does not apply here.

**Client-side validation is sufficient.** Either Guard rules against the synthesized CloudFormation template, or cdk-nag Aspects embedded in the generated CDK code. Guard is the better fit because it operates on the CloudFormation template (a stable format) rather than requiring the Aspects to be included in the generated code (which the agent could omit).

### The Three Layers in DOH

**Policy** — A Guard rule or equivalent validation check. DOH would curate these, not expose the rule language to users.

**Policy Pack** — A named collection of rules. For internal apps, the relevant packs are:

- **Cost Guardrails** — max CPU/memory per service, max number of services per environment, instance size limits. Prevents a vibe-coded prototype from accidentally running on 4096 CPU.
- **Internal Network Only** — no public-facing endpoints, VPC-only access, no open security groups. Ensures internal tools stay internal.
- **Auto-Cleanup** — TTL enforcement, require auto-destroy after N days. Prevents forgotten prototypes from accumulating cost.
- **Security Basics** — encryption at rest, no hardcoded credentials in environment variables, least-privilege task roles. Lightweight hygiene, not regulatory compliance.

Regulatory compliance packs (PCI DSS, HIPAA, CIS benchmarks) are less relevant for the internal apps use case. They could be offered later if DOH expands to customer-facing deployments.

**Policy Group (the missing piece)** — A domain model addition:

- A `PolicyPack` entity, belonging to Organization, referencing a set of rules or a built-in pack
- An assignment linking `PolicyPack` to `Environment` (or Organization-wide for all environments)
- Evaluation in the deployment pipeline: after `cdk synth`, before `cdk deploy`, run all assigned packs against the template

This is independent of the per-environment configuration problem identified in `stack_concept_analysis.md`. Policy groups assign validation rules to environments; per-environment config assigns deployment parameters to environments. They are separate concerns. The Environment entity already exists and is sufficient for policy assignment.

### User Experience

DOH's target users are not writing Guard rules. These are engineers who vibe-code internal tools and want them deployed — they don't know or care about CloudFormation Guard syntax.

The interface should be toggle-based at the environment level: "Max CPU per service: 1024," "No public endpoints," "Auto-destroy after 7 days." DOH translates those selections into Guard rules internally. The user sees a list of checks that passed or failed before deployment proceeds — not the policy language.

The natural owner of these settings is the organization admin, not the developer deploying the app. An admin configures guardrails for the "dev" environment (loose limits, TTL enforced) and the "production" environment (stricter limits, no TTL). Developers deploy through the chat and see pass/fail results without needing to understand the underlying policy system.

### Pipeline Integration

Policy validation fits naturally into a staged deployment pipeline:

1. **Analyze** — scan repo, produce app config
2. **Generate** — produce CDK code + parameter file
3. **Validate** — run assigned policy packs against synthesized CloudFormation template
4. **Preview** — show the user what will be created/modified/destroyed
5. **Execute** — `cdk deploy`, build and push image, start service
6. **Verify** — health checks, report URL

The validate step blocks progression if any mandatory policy fails. Advisory policies produce warnings but allow the user to proceed.

---

## Pulumi Enterprise Features — Relevance to DOH

Beyond policy enforcement, Pulumi's enterprise tier offers features worth evaluating for the internal apps use case:

| Feature | Pulumi | DOH Status | Relevance for Internal Apps |
|---|---|---|---|
| **Policy enforcement** | CrossGuard with pre-built compliance packs | Not implemented | **High** — but as cost/network guardrails, not regulatory compliance |
| **TTL stacks** | Auto-destroy after set duration | Not implemented | **High** — internal prototypes churn fast, prevents cost accumulation |
| **Drift detection** | Detect and remediate infrastructure drift | Not implemented | **Low** — manual tweaks to internal tools rarely matter |
| **Audit logs** | Who deployed what, when | Partial (deployment + LLM usage tracking) | **Medium** — mostly for cost attribution ("who deployed the expensive one?") |
| **RBAC** | Per-stack role-based access | Basic (admin/member/viewer per org) | **Medium** — per-environment access control is useful once orgs scale |
| **Scheduled deployments** | Cron-based deploys | Not implemented | **Low** — internal apps deploy on demand via chat |
| **Secrets rotation** | Dynamic credentials, DB secret rotation | Not implemented (uses Secrets Manager directly) | **Low** — internal apps rarely need rotation automation |
| **Self-hosting** | Run Pulumi Cloud on-prem | N/A | **Low** — DOH is SaaS by design |

The highest-value features for the internal apps use case are **TTL stacks** and **cost-focused policy enforcement**. Both address the same core problem: vibe-coded apps are easy to create and easy to forget, and forgotten infrastructure costs money.
