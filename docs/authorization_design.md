# Authorization Design

This document captures the design decisions for DevOps Hero's authorization system. It covers two authorization domains:

- **Platform access** — who can manage workspaces, deploy apps, administer infrastructure on the DOH platform
- **App access** — who can use the deployed internal tools (the apps DOH deploys into customer VPCs)


## Problem Statement

DevOps Hero needs to control access at two levels:

**Platform level:**
- Someone not in finance shouldn't access the finance workspace
- Within a workspace, some people can deploy, others can create apps, others can administer everything
- Deploying to production should be restricted to a smaller set of people than deploying to staging

**App level:**
- Deployed internal apps need access control without app authors having to implement it
- Some apps should be available to all employees, others restricted to specific groups
- Sensitive routes within an app may need stricter access than the app's default


## Approach: RBAC with Permissions + Groups

We evaluated six approaches:

- **Flat Org-Level RBAC** — Roles only at the organization level. Too coarse; no workspace isolation.
- **Resource-Scoped RBAC** — Roles at workspace/environment level. Intuitive but role definitions are coarse.
- **Fine-Grained Permissions** — Individual permission assignments per user per resource. Maximum flexibility, management nightmare.
- **RBAC with Permissions** — Roles are named bundles of permissions, assigned at resource scope. Best balance of manageability and precision.
- **ABAC (Attribute-Based)** — Decisions based on user/resource/context attributes. Very expressive, hard to reason about, overkill for this use case.
- **Policy-Based (IAM-Style)** — Policy documents describing allowed actions on resource patterns. Powerful but complex; our users already wrestle with IAM and don't want more of it.

**Decision: RBAC with Permissions + Groups.** Roles are named permission bundles. Groups are collections of users. Groups get assigned roles on specific resources.


## Considered Alternative: ABAC with Tags and Centralized Policy

Based on [Tailscale's "RBAC Like It Was Meant to Be"](https://tailscale.com/blog/rbac-like-it-was-meant-to-be). The post argues that most RBAC implementations degenerate into per-resource ACLs with extra steps. The alternative splits access control into three layers:

- **Roles** — Actual job functions on identities (Developer, Finance Analyst, DevOps Engineer). Describe who people are, not what they can access. Managed by HR/identity provider.
- **Tags** — Labels on resources applied by resource owners (production, staging, finance, sensitive). Describe what the resources are.
- **Policy** — A centralized formula mapping (role + tag) to allowed actions. The only place access rules live.

In this model, you never create per-resource bindings. Access is derived: "anyone with role Developer can deploy to anything tagged staging." When a person changes jobs, you change their role and all access recalculates automatically.

Applied to DOH, this would mean tagging workspaces, environments, and apps, then writing a centralized policy like:

```
role:developer       + tag:staging    -> can deploy
role:devops-engineer + tag:production -> can deploy
role:finance-dept    + tag:finance    -> developer access on workspace
role:finance-analyst + tag:finance    -> can use app
```

**Why we didn't choose this:**

- DOH's target audience is small-to-medium teams who want to ship internal tools fast. A centralized policy formula is more abstract than "add group to workspace, pick role."
- The UI for managing a centralized policy is harder to build and harder for users to understand than per-resource access tabs.
- Tag governance is its own problem — someone must manage a tag taxonomy and control who can apply which tags.
- Explicit per-resource bindings are simpler to implement and reason about at DOH's current scale.

**When to revisit:** If customers outgrow explicit bindings (many workspaces, frequent job changes, complex cross-cutting access patterns), ABAC with tags could be layered on as an advanced mode alongside the existing binding-based model.


## Core Concepts

- **Identity** — A person known to DOH within an organization. Created automatically when someone first authenticates via WorkOS SSO (corporate IdP). Stores email, name, and IdP subject. This is a lightweight record — no onboarding flow, no platform credentials.
- **Platform User** — An identity that additionally has an OrganizationMembership, granting access to the DOH platform itself (managing workspaces, deploying apps, etc.). An identity without an OrganizationMembership can still use deployed apps but never sees the DOH platform UI.
- **Group** — A named collection of identities. Administrative container for assigning access. Groups are organization-scoped. Groups contain both platform users and app-only identities — the same group model governs both platform and app access.
- **Role** — A named bundle of permissions, pre-defined by the platform. Roles are resource-type-specific (workspace roles, environment roles, and app roles are separate concepts).
- **Role Binding** — The three-way relationship: Group G has Role R on Resource X.
- **Resource** — The thing being protected: Workspaces, Environments, and Apps.


## Decision: Groups, Not Teams

We use the term "Group" instead of "Team."

**Rationale:** "Team" is psychologically overloaded. People wonder "do I really want to belong to that Team?" — it implies social identity and allegiance. "Group" is a neutral administrative container. It's also the term users already know from IAM, Azure AD, and LDAP, so there's zero conceptual translation.


## Decision: Roles Are Pre-Defined by the Platform

Roles are not user-configurable. The platform ships a fixed set of roles per resource type, similar to GitHub's repository roles (Read, Triage, Write, Maintain, Admin).

**Rationale:** DevOps Hero's audience wants to deploy apps, not design permission taxonomies. Fewer roles means fewer footguns. Custom roles can be added later; removing them is harder.

Proposed roles (not yet finalized):

- **Workspace roles**: `viewer`, `developer`, `admin`
- **Environment roles**: `viewer`, `deployer`, `approver`
- **App roles**: `user` (can access the app, subject to route overrides)

Each role implies a fixed, documented set of capabilities. App roles only apply when the app's access level is "restricted" — for "VPC-open" and "authenticated" apps, no role bindings are checked.


## Decision: Roles Are Resource-Type-Specific

Workspace roles and environment roles are separate enums, not a universal set.

**Rationale:** "Developer" makes sense on a workspace (create apps, edit config) but is meaningless on an environment. "Deployer" makes sense on an environment but is odd on a workspace. Resource-type-specific roles are more precise and avoid overloading role names with context-dependent meanings.


## Decision: One Model Per Resource Type

Role bindings are stored as separate models per resource type: `WorkspaceRoleBinding(group, role, workspace)`, `EnvironmentRoleBinding(group, role, environment)`, and `AppRoleBinding(group, role, app)`.

**Rationale:** Simpler than a polymorphic generic model. Each binding model has its own role enum, database-level foreign key constraints, and is straightforward to query. Debatable decision — a polymorphic model would reduce table count but at the cost of losing FK constraints and complicating queries.


## Organization-Level Roles

Deferred. Some actions (connecting AWS accounts, managing groups, inviting users) aren't scoped to a workspace or environment. How org-wide admin roles interact with resource-scoped roles is an open question.


## App Access: Sidecar Proxy

Deployed apps are protected by an authorization sidecar that DOH injects into the ECS task definition. The app container binds to localhost only; the sidecar is the only container exposed to the ALB. All requests to the app flow through the sidecar.

### Identity for App Users

App users (employees accessing deployed internal tools) authenticate via WorkOS SSO, which federates with the company's corporate IdP (Okta, Azure AD, Rippling, etc.).

When someone first authenticates through the sidecar, DOH auto-creates a lightweight identity record (email, name, IdP subject). No onboarding flow, no platform credentials. These identities can be added to Groups by an admin. The person never sees the DOH platform UI unless they are also a platform user.

A platform user (someone who manages workspaces, deploys apps) is the same identity record with an additional OrganizationMembership. There is one identity model, not two separate tables.

### App Access Levels

Each app has a configurable access level:

- **VPC-open** — No authentication. Anything with VPC network connectivity can reach the app. The sidecar is either in passthrough mode or not injected. For low-sensitivity tools like internal documentation or status dashboards.
- **Authenticated** — The sidecar requires SSO authentication but performs no group checks. Any employee who can authenticate through the corporate IdP gets in. For broadly available internal tools.
- **Restricted** — The sidecar requires SSO authentication AND checks group-based access rules. Only specific groups can access the app. For sensitive tools like finance, HR, or admin panels.

The default access level for new apps is "authenticated" — protected but not locked down.

### Route-Level Overrides

On top of the app-level access setting, specific route patterns can have stricter rules:

```
App "financial-reports"
  default: authenticated (any SSO-authenticated user)

  route overrides:
    /executive-dashboards/*  -> groups: [executives]
    /admin/*                 -> groups: [finance-admins]
```

Most apps will only set the app-level default and never configure route overrides. Per-route restrictions are opt-in for the apps that need them.

### Sidecar Request Flow

1. Request arrives at the sidecar
2. If access level is VPC-open: proxy to app container, done
3. Is the user authenticated? If not, redirect to WorkOS SSO flow
4. On successful auth, create or resolve the lightweight identity record
5. If access level is authenticated: proxy to app container, done
6. If access level is restricted: check if the identity belongs to any group with access to this app
7. Check route overrides: does the request path match a route pattern with stricter group requirements?
8. Allow or deny


## AppPermissionRequest Approval

`AppPermissionRequest` governs what AWS resources an app's ECS task role can access (S3 buckets, DynamoDB tables, etc.). These requests require approval because they expand the app's blast radius in the customer's AWS account.

### Who Can Submit

Anyone with `developer` or `admin` role on the app's **workspace**. They understand what the app needs because they built it.

### Who Can Approve

Anyone with `approver` role on the target **environment** (or org admin). Approval authority comes from the environment, not the workspace, because the risk is tied to where the permissions are applied. The same app requesting `s3:GetObject` in staging is low risk; `s3:*` in production is high risk.

### Roles Are Independent

`approver` and `deployer` are independent environment roles, not hierarchical. A security reviewer can approve IAM changes without having deploy access. A developer can deploy code without being able to expand AWS permissions. A group can hold both roles on the same environment — members get the union of all roles from all their groups.

### Flow

1. Developer (workspace `developer` or `admin`) creates an `AppPermissionRequest` in draft status
2. The permissions agent provides blast radius assessment for the approver
3. Approver (environment `approver` or org admin) reviews and approves → status becomes `approved_pending_apply`
4. DOH applies the IAM policy changes → status becomes `applied`


## UI Flows

Access management is available from two directions.

### Primary: Resource-Centric View

The most common question is "who has access to this workspace?" so the primary management surface lives on each resource's page.

On a workspace page, an "Access" tab shows groups with their roles:

```
Workspace "Finance" -> Access tab

  Group              Role
  -------------------------------------------
  finance-devs       developer    [change] [remove]
  finance-managers   admin        [change] [remove]
  platform-team      admin        [change] [remove]

  [+ Add group]
```

Same pattern on an environment page:

```
Environment "Staging" -> Access tab

  Group              Role
  -------------------------------------------
  finance-devs       deployer     [change] [remove]
  platform-team      deployer     [change] [remove]

  [+ Add group]
```

Adding access: click "Add group", pick a group from a dropdown, pick a role, save.

### Secondary: Group-Centric View

On the org settings page, managing groups. Clicking into a group shows its members and a summary of all its role bindings across the organization:

```
Group "finance-devs" -> Members: Alice, Bob

  Access grants:
  -------------------------------------------
  Workspace "Finance"        developer
  Environment "Staging"      deployer

  [+ Add access]
```

This view is useful for auditing ("what can this group do across the whole org?") but is the secondary path, not where most day-to-day management happens.

### Example Flow: Onboarding a New Team

A concrete scenario — Acme hires a data team and wants to give them their own workspace:

1. Org admin goes to Org Settings, Groups, Create group "data-team"
2. Adds members to the group (picks from existing org users)
3. Goes to Workspaces, Create workspace "Data"
4. On the new workspace, opens the Access tab, Add group, picks "data-team", assigns role "developer"
5. Goes to Environment "Staging", Access tab, Add group, picks "data-team", assigns role "deployer"

Now the data team can create apps in their workspace and deploy to staging. They can't see the Finance workspace and can't deploy to production.
