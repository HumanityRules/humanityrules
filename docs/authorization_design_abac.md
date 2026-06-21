# Authorization Design: ABAC

This document describes the ABAC (Attribute-Based Access Control) authorization system for Humanity Rules.

All concepts in this document — policies, identity attributes, resource tags, groups, and grants — are organization-scoped. A policy in one organization never affects resources or identities in another.

The same two authorization domains apply:

- **Platform access** — who can manage workspaces, deploy apps, administer infrastructure on the HUMR platform
- **App access** — who can use the deployed internal tools (the apps HUMR deploys into customer VPCs)


## Why ABAC

The RBAC model requires explicit per-resource bindings: "Group G has Role R on Resource X." This works for small teams but becomes unwieldy as organizations grow. Adding a new workspace means manually granting access to every relevant group. Changing someone's job function means updating bindings across many resources.

ABAC derives access from attributes. Tag a workspace `finance`, tag a person `department:finance`, and a single policy grants access. When someone moves departments, change their attributes — access recalculates everywhere automatically.

We believe we can build a UI that makes ABAC as approachable as explicit bindings while gaining the expressiveness and scalability benefits.


## Three Layers

ABAC in HUMR is built from three layers:

- **Identity attributes** — Key-value pairs on people. Describe who they are: department, job function, custom labels. Managed within HUMR.
- **Resource tags** — Key-value pairs on resources. Describe what the resource is: `sensitivity:high`, `domain:finance`, `tier:production`. Applied by resource owners through the HUMR UI.
- **Policies** — Rules that map (identity attributes + resource tags) to allowed actions. The single place where access decisions live.

Access is never granted directly to a person on a resource. Access is always derived: if your attributes match a policy, and the resource's tags match the same policy, you get the actions that policy allows.


## Identity Attributes

An identity attribute is a key-value pair attached to a person. A person can have multiple attributes. Examples:

- `department:engineering`
- `job-function:developer`
- `team:data-platform`
- `clearance:sensitive`

A single person might carry all four of these simultaneously. All of their attributes participate in policy evaluation — if any policy's identity condition matches a subset of the person's attributes, that policy applies.

### Attribute Sources

An identity's effective attributes are the union of:

- **System attributes** — Automatically applied by HUMR based on the identity's state (see below).
- **Direct attributes** — Assigned to the person explicitly by an org admin.
- **Group-inherited attributes** — Coming from groups the person belongs to (see Groups below).

### System Attributes

Some attributes are applied automatically by HUMR and cannot be manually assigned or removed. These allow policies to reference fundamental identity properties:

- `authenticated:true` — Applied to any identity that has completed SSO authentication. This is the primary way to write policies that apply to "any logged-in user."

System attributes participate in policy evaluation the same way as direct and group-inherited attributes. They appear in the UI as a distinct source (e.g., "system") and are not editable.

All attribute sources are equal for policy evaluation. The UI must distinguish between direct, group-inherited, and system attributes, the same way app tags distinguish direct from workspace-inherited tags.

### Bootstrapping

When a new organization is created, HUMR bootstraps the ABAC system:

1. Assigns `org-role = admin` as a direct attribute to the organization creator.
2. Creates seed policies that grant all platform actions to identities with `org-role = admin` on resource condition `*` (all resources). These are normal policies — the org admin can edit or delete them (at their own risk).

From this point, the org admin can create attributes, tags, groups, and policies through the normal UI. The org admin can assign `org-role = admin` to other identities through the attribute management UI — this is how additional org admins are created. There is no special-cased admin role outside of the ABAC model — org admin is just an identity attribute like any other, resolved through the same policy evaluation.

### Groups

Groups still exist in the ABAC model, but their role changes. In RBAC, groups were assigned roles on specific resources. In ABAC, groups are **attribute containers** — administrative shortcuts for assigning attributes to many people at once.

A group has:

- **Members** — The people in the group.
- **Attributes** — Key-value pairs attached to the group.

Every member of the group inherits the group's attributes. If group "Finance Team" has attribute `department:finance`, every member of that group carries `department:finance` in their effective attribute set.

A person can belong to multiple groups. Their effective attributes are the union of their direct attributes and all attributes inherited from all their groups.

The same attribute key can have multiple values from different sources. For example, a person with direct attribute `team = security` who inherits `team = finance` from a group has both values in their effective set. Policy conditions match against any value for the key — a policy requiring `team = finance` matches this person. Multiple values for the same key are OR-ed during evaluation.

Example:

- Alice has direct attribute `clearance:executive`
- Alice is a member of group "Finance Team" which has `department:finance` and `team:finance-eng`
- Alice is a member of group "Senior Staff" which has `seniority:senior`
- Alice's effective attributes: `clearance:executive`, `department:finance`, `team:finance-eng`, `seniority:senior`

### Attribute Management

Attributes (both on identities and on groups) are managed manually within HUMR by org admins. There is no attribute taxonomy enforced by the system — admins create whatever keys and values make sense for their organization.

### App-Only Identities

Lightweight identities (auto-created on first SSO through the policy proxy) receive attributes the same way as platform users — directly assigned by org admins, or inherited through group membership.


## Resource Tags

A resource tag is a key-value pair attached to a workspace, environment, or app. Examples:

- `domain:finance`
- `sensitivity:high`
- `tier:production`
- `team:data-platform`

### What Gets Tagged

All three resource types: workspaces, environments, and apps.

A resource can have multiple tags. An app might be tagged both `domain:finance` and `sensitivity:high`.

### Tag Inheritance

**Apps inherit tags from their parent workspace.** If workspace "Finance" is tagged `domain:finance`, all apps within that workspace automatically carry `domain:finance`. This inheritance is automatic and cannot be overridden — the app always has at least its workspace's tags, plus any tags applied directly to the app itself.

**Environments do not inherit tags.** Environment tags are always explicit. This is intentional: environments are account-scoped, not workspace-scoped — multiple workspaces deploy apps to the same environment. A `staging` environment and a `production` environment have very different access requirements that are unrelated to which workspace an app belongs to. Inheriting workspace tags onto environments would conflate "who works in this domain" with "who can touch this tier," which are separate concerns.

### Tag UI

The tag editor appears on each resource's settings page. For apps, the UI must distinguish between:

- **Direct tags** — Applied to this app specifically. Editable.
- **Inherited tags** — Coming from the parent workspace. Shown with an "inherited from workspace" indicator. Not editable on the app — must be changed on the workspace.

Both direct and inherited tags participate in policy evaluation identically.

### Tag Governance

Tags are freeform key-value pairs. There is no enforced taxonomy. Org admins are responsible for establishing naming conventions for their organization.

An "Add tag" button appears on every resource where the current user has permission to manage tags. If the user lacks permission, the button is visible but greyed out — making the capability discoverable without hiding it.


## Actions

Actions describe what someone can do. They are resource-type-specific. The action prefix (`workspace:`, `environment:`, `app:`) determines which resource type the policy evaluates against.

- **Workspace actions:**
  - `workspace:view` — See the workspace and its contents
  - `workspace:edit` — Edit workspace settings, app configuration, create apps
  - `workspace:admin` — Full workspace control, including managing tags on the workspace and its apps. Implies `workspace:view` and `workspace:edit`.
- **Environment actions:**
  - `environment:view` — See the environment and its status
  - `environment:deploy` — Deploy apps to this environment
  - `environment:approve` — Approve AppPermissionRequests targeting this environment
  - `environment:admin` — Full environment control, including managing tags. Implies `environment:view`, `environment:deploy`, and `environment:approve`.
- **App actions:**
  - `app:use` — Access the deployed app through the policy proxy

Some actions are supersets of others (as noted above). When a policy grants `workspace:admin`, the identity implicitly has all other workspace actions. The action catalog can grow over time without restructuring the authorization model.

### Platform Visibility vs. App Access

`app:use` is exclusively a runtime action evaluated by the policy proxy. It controls who can open and use the deployed application, not who can see the app listed in the HUMR platform.

Currently, platform visibility of apps and datastores is derived from `workspace:view` on the parent workspace. If a user has `workspace:view`, they can see all apps and datastores within that workspace on the dashboard and workspace detail pages. There is no `app:view` action — visibility is all-or-nothing at the workspace level.

**Limitation:** There is no way to hide a specific app from a user who has `workspace:view` on its workspace. A future `app:view` action would allow per-app platform visibility control — for example, denying a user or group from seeing sensitive apps inside workspaces they otherwise have access to. Until then, the workspace boundary is the finest granularity for platform visibility.


## Policies

A policy is a rule that grants a set of actions when both an identity attribute condition and a resource tag condition are met.

### Structure

A policy is a tuple:

- **Name** — A human-readable label. Required. Unique per organization.
- **Identity condition** — A match on identity attributes. Examples: `department = finance`, `job-function = developer`, `team = data-platform`. The special value `*` matches any identity (authenticated or not).
- **Resource condition** — A match on resource tags. Examples: `domain = finance`, `tier = staging`. The special value `*` matches any resource of the target type regardless of tags.
- **Actions** — The actions this policy allows or denies. A `!` prefix denies the action. Examples: `workspace:view`, `environment:deploy`, `!environment:deploy`.

A policy applies when the identity's attributes satisfy the identity condition AND the resource's tags (including inherited tags) satisfy the resource condition. Actions without a prefix are grants. Actions with a `!` prefix are denials.

**One resource type per policy.** All actions in a single policy must target the same resource type. The action prefix (`workspace:`, `environment:`, `app:`) determines which resource type the policy evaluates against. You cannot mix `workspace:view` and `environment:deploy` in the same policy — that requires two separate policies.

Both identity conditions and resource conditions support multiple clauses, AND-ed together. For example, a policy can require `department = finance AND clearance = sensitive`. OR is expressed by creating multiple policies.

### Self-Referential Conditions

Policies can also express a constraint that links an identity attribute to a resource tag — "the identity's attribute X must equal the resource's tag Y." This is written with the special `$identity.<key>` and `$resource.<key>` references in place of a literal value:

- **Identity condition:** `username = $resource.owner`
- **Resource condition:** `app-type = personal-assistant`
- **Actions:** `app:use`

This single policy grants every person access to resources tagged as their own — the canonical "owned-by" pattern. Without self-referential conditions, expressing this requires one policy per (identity, resource) pair.

Semantics:

- Either side of a clause can be a literal or a reference. `username = $resource.owner` and `$identity.username = owner-placeholder` are different constructs; use `$resource.<key>` on the value side of an identity clause.
- The reference is evaluated per-request, against the concrete identity and resource being checked.
- If the referenced attribute or tag is absent, the clause does not match.
- All other evaluation rules apply unchanged (deny-overrides, union-of-grants, multiple clauses AND-ed).

The policy editor exposes `$resource.<key>` as a value choice in the identity condition's value dropdown, populated from the set of tag keys that exist on the selected resource type.

### Examples

Grant all finance department members view access to finance workspaces:

- **Identity condition:** `department = finance`
- **Resource condition:** `domain = finance` (on workspace)
- **Actions:** `workspace:view`

Grant developers deploy access to staging environments:

- **Identity condition:** `job-function = developer`
- **Resource condition:** `tier = staging` (on environment)
- **Actions:** `environment:deploy`

Grant DevOps engineers full environment control in production:

- **Identity condition:** `job-function = devops-engineer`
- **Resource condition:** `tier = production` (on environment)
- **Actions:** `environment:view`, `environment:deploy`, `environment:approve`

Grant all authenticated employees access to apps tagged as internal tooling:

- **Identity condition:** `authenticated = true`
- **Resource condition:** `visibility = all-employees` (on app)
- **Actions:** `app:use`

Restrict a sensitive app to specific teams:

- **Identity condition:** `team = data-platform`
- **Resource condition:** `domain = finance`, `sensitivity = high` (on app)
- **Actions:** `app:use`

Prevent contractors from deploying to or approving changes in production, regardless of other policies:

- **Identity condition:** `employment-type = contractor`
- **Resource condition:** `tier = production` (on environment)
- **Actions:** `!environment:deploy`, `!environment:approve`

Grant every employee access to their own personal assistant, via one global policy:

- **Identity condition:** `username = $resource.owner`
- **Resource condition:** `app-type = personal-assistant` (on app)
- **Actions:** `app:use`

### Evaluation

- **Deny by default.** If no policy grants the requested action, access is denied.
- **Deny-overrides.** If any applicable policy denies an action (`!action`), access is denied regardless of other policies that grant it. This ensures safety constraints can't be overridden by a permissive policy elsewhere.
- **Union of grants.** When multiple policies grant different actions on the same resource, the identity gets the union of all granted actions.

### Policy Authoring

Policies are authored through a structured UI — there is no DSL or code editor. The UI presents the policy fields as form inputs with dropdowns and auto-complete based on existing attribute keys and tag keys in the organization.

The policy editor requires selecting a resource type first (workspace, environment, or app). This selection constrains both the tag and action dropdowns:

- The **resource condition** dropdown only shows tags that exist on resources of that type, organized as a hierarchy: resource type → individual resources the editor has visibility into.
- The **action** dropdown only shows actions for that resource type.

This prevents invalid combinations (e.g., `workspace:view` on an environment tag) and helps the editor see which concrete resources their policy will affect.

**Who can author policies:**

- **Org admins** (identities with `org-role = admin`) can create, edit, and delete any policy.
- **Delegated editors** — Org admins can grant specific identities the ability to create and modify policies scoped to particular resource tags, through Policy Editor Grants (see below).

### Policy Editor Grants

Policy Editor Grants are a dedicated delegation mechanism, separate from ABAC policies. They allow org admins to let specific people manage a subset of policies without granting full org admin access.

A Policy Editor Grant is a tuple:

- **Identity condition** — Which identities receive editing rights. Uses the same attribute-matching syntax as policies (e.g., `team = finance-eng`).
- **Resource tag scope** — Which policies the grant covers, defined by the resource tags those policies target. For example, scope `domain = finance` means the grantee can create, edit, and delete policies whose resource condition includes `domain = finance`.

A delegated editor can only manage policies whose resource condition falls entirely within their granted scope. They cannot create policies that reference resource tags outside their scope. They can use `!` (deny) actions — since the scope constraint already limits the blast radius of any deny to resources within their domain of responsibility.

Example:

- Org admin creates a Policy Editor Grant:
  - **Identity condition:** `team-lead = finance`
  - **Resource tag scope:** `domain = finance`
- The finance team lead can now create policies like "IF identity `department = finance` AND resource `domain = finance` THEN `workspace:view`"
- The finance team lead cannot create a policy targeting `domain = engineering` or `tier = production`

Policy Editor Grants are managed in a dedicated section of the org settings UI, visible only to org admins.

```
Org Settings -> Policy Delegation

  Policy Editor Grants:
  ──────────────────────────────────────────────────────────

  "Finance team lead can manage finance policies"     [edit] [delete]
    WHO: identity team-lead = finance
    SCOPE: policies targeting domain = finance

  "Platform lead can manage infra policies"           [edit] [delete]
    WHO: identity team-lead = platform
    SCOPE: policies targeting domain = infrastructure

  [+ Create grant]
```


## App Access: Sidecar Integration

The policy proxy that protects deployed apps evaluates ABAC policies by calling a central Policy Decision Point (PDP) service, rather than evaluating policies locally.

### Access Levels Are Subsumed

The three access levels from the RBAC model (VPC-open, authenticated, restricted) are no longer separate configuration. All three are expressed as policies:

- **Equivalent of "VPC-open"** — A policy with identity condition `*` (any identity, authenticated or not) granting `app:use`. The policy proxy reads this at configuration time and optimizes to passthrough mode — no per-request evaluation needed.
- **Equivalent of "authenticated"** — A policy with identity condition `authenticated = true` granting `app:use`.
- **Equivalent of "restricted"** — Only specific policies grant `app:use` (e.g., scoped to particular identity attributes).

### Default App Policy

Every new app is created with a default policy. This ensures the app's access posture is always explicitly visible in the UI — there is no implicit or hidden access level.

The default policy for new apps is:

- **Identity condition:** `*`
- **Actions:** `app:use`

This is the most permissive posture: anyone with network access can reach the app, and the policy proxy operates in passthrough mode. Admins can narrow this after creation — changing `*` to `authenticated = true` to require SSO, or to specific attributes for restricted apps.

### Route-Level Overrides

Route overrides are policies with an additional route pattern field. They add restrictions on top of the base app policy — they can only narrow access, never widen it.

A route override is a policy tuple with one extra field:

- **Route pattern** — A URL path pattern (e.g., `/admin/*`, `/api/v2/reports/*`).
- **Identity condition** — Additional identity requirements for this route.
- **Actions** — Always `app:use` (inherits from the app context).

**Additive restriction:** For a request matching a route pattern, the identity must satisfy both the base app policy AND the route override's identity condition. A route override cannot grant access to someone who doesn't already have base app access.

Example:

```
App "financial-reports"
  tags: domain=finance, visibility=all-employees

  base policy: authenticated = true -> app:use

  route overrides:
    /executive-dashboards/*  -> additionally requires: clearance = executive
    /admin/*                 -> additionally requires: job-function = finance-admin
```

A request to `/executive-dashboards/q1` requires `authenticated = true` (from the base policy) AND `clearance = executive` (from the route override). A request to `/reports/q1` only requires `authenticated = true`.

### Sidecar Request Flow

1. Sidecar starts up and fetches the app's policies from the PDP
2. If the app has a `*` policy (unconditional `app:use` grant): policy proxy enters passthrough mode, all requests proxy directly to the app container, done
3. Request arrives at the policy proxy
4. Is the user authenticated? If not, redirect to SSO flow
5. On successful auth, resolve the identity and its attributes
6. Send policy evaluation request to the PDP: identity attributes, app tags (direct + inherited), request path, requested action (`app:use`)
7. PDP evaluates the base app policies. If denied, return deny.
8. PDP checks if the request path matches any route override. If it does, evaluate the route override's identity condition as an additional requirement.
9. PDP returns allow or deny
10. Sidecar proxies or rejects the request

Policy changes require a policy proxy restart to take effect. The policy proxy does not poll or receive push updates.


## AppPermissionRequest Approval

Approval authority for `AppPermissionRequest` (expanding an app's AWS IAM permissions) is expressed through ABAC policies.

- **Who can submit:** Anyone whose attributes match a policy granting `workspace:edit` or `workspace:admin` on the app's workspace tags. (If you can build in this workspace, you can request permissions for your app.)
- **Who can approve:** Anyone whose attributes match a policy granting `environment:approve` on the target environment's tags. A security engineer with `job-function = security-engineer` can be granted `environment:approve` on environments tagged `tier = production` without needing deploy access.

The flow remains the same:

1. Developer creates an `AppPermissionRequest` in draft status
2. Permissions agent provides blast radius assessment
3. Approver reviews and approves (status becomes `approved_pending_apply`)
4. HUMR applies the IAM policy changes (status becomes `applied`)


## Access Explainer

ABAC policies are harder to reason about than explicit bindings. To compensate, HUMR provides an access explainer — a tool that answers "why does (or doesn't) this identity have access to this resource?"

### Capabilities

- **Forward query:** Given an identity, show all resources they can access and which policies grant that access.
- **Reverse query:** Given a resource, show all identities that can access it and which policies grant that access.
- **Specific check:** Given an identity and a resource, explain the full evaluation: which policies matched, which denied, what the final decision is and why.
- **What-if simulation:** "If I add tag X to this resource, who gains or loses access?" or "If I change this person's department attribute, what access changes?"

### Where It Appears

- On each resource's page, a "Who has access" panel showing the evaluated result (not raw policies, but the resolved list of people with access).
- On each identity's profile, a "What can they access" panel.
- A standalone policy simulator in org settings for what-if analysis.


## UI Flows

### Tag Editor (on resource settings page)

On a workspace:

```
Workspace "Finance" -> Settings -> Tags

  Tags:
  domain = finance          [edit] [remove]
  team = finance-eng        [edit] [remove]

  [+ Add tag]
```

On an app within that workspace:

```
App "financial-reports" -> Settings -> Tags

  Inherited from workspace "Finance":
    domain = finance          (inherited)
    team = finance-eng        (inherited)

  Direct tags:
    sensitivity = high        [edit] [remove]
    visibility = all-employees [edit] [remove]

  [+ Add tag]
```

On an environment:

```
Environment "Production" -> Settings -> Tags

  Tags:
  tier = production         [edit] [remove]

  [+ Add tag]
```

### Identity Attributes (org settings)

```
Org Settings -> People -> Alice

  System attributes:
    authenticated = true        (system)

  Direct attributes:
    org-role = admin            [edit] [remove]
    clearance = executive       [edit] [remove]

  Inherited from groups:
    department = finance        (from "Finance Team")
    team = finance-eng          (from "Finance Team")
    seniority = senior          (from "Senior Staff")

  Groups:
    Finance Team                [remove]
    Senior Staff                [remove]
    [+ Add to group]

  [+ Add attribute]
```

### Group Editor (org settings)

```
Org Settings -> Groups -> "Finance Team"

  Attributes:
    department = finance        [edit] [remove]
    team = finance-eng          [edit] [remove]

    [+ Add attribute]

  Members:
    Alice                       [remove]
    Bob                         [remove]
    Carol                       [remove]

    [+ Add member]
```

### Policy Editor (org settings)

```
Org Settings -> Policies

  Policies:
  ──────────────────────────────────────────────────────────

  "Finance team workspace access"                     [edit] [delete]
    IF identity department = finance
    AND resource domain = finance (workspace)
    THEN workspace:view, workspace:edit

  "Developers can deploy to staging"                  [edit] [delete]
    IF identity job-function = developer
    AND resource tier = staging (environment)
    THEN environment:deploy

  "No contractor access to production"                [edit] [delete]
    IF identity employment-type = contractor
    AND resource tier = production (environment)
    THEN !environment:deploy, !environment:approve

  [+ Create policy]
```

### Access Explainer (on resource page)

```
Workspace "Finance" -> Who has access

  Identity           Actions                  Granted by
  ──────────────────────────────────────────────────────────
  Alice (finance)    view, edit               "Finance team workspace access"
  Bob (finance)      view, edit, admin        "Finance team workspace access"
                                              + "Finance admin access"
  Carol (eng)        view                     "Org-wide read access"

  [Simulate access for another user...]
```

### Example Flow: Onboarding a New Team

Scenario — Acme hires a data team and wants to give them their own workspace:

1. Org admin goes to Org Settings, Groups, creates group "Data Platform Team". Adds attributes `team = data-platform` to the group.
2. Adds the new hires as members of the group. They all inherit `team = data-platform`.
3. Org admin goes to Workspaces, creates workspace "Data Platform". Tags it `domain = data-platform`.
4. Org admin goes to Environments, tags "Staging" environment as `tier = staging` (if not already tagged).
5. Org admin creates two policies:
   - IF identity `team = data-platform` AND resource `domain = data-platform` (workspace) THEN `workspace:view`, `workspace:edit`
   - IF identity `team = data-platform` AND resource `tier = staging` (environment) THEN `environment:deploy`
6. Done. Any future hire just needs to be added to the "Data Platform Team" group — they inherit the attributes and all matching policies apply automatically.


## Deferred Features

- **IdP sync** — Sync identity attributes from corporate IdPs (Okta, Azure AD, Rippling) via WorkOS directory sync (SCIM). Synced attributes would be read-only in the HUMR UI. Requires a mapping configuration UI to translate provider-specific fields to HUMR attribute keys.
- **Tag key registry** — A controlled vocabulary of allowed tag keys, managed by org admins, to prevent drift and typos. Start freeform for now.
- **Policy versioning** — Audit log of policy changes with the ability to view and restore previous versions.
- **Bulk group membership import** — CSV import of members into groups, for organizations too large for manual member-by-member assignment before IdP sync is available.
- **Group management delegation** — Allow org admins to delegate group management (adding/removing members, editing group attributes) to specific identities. This would be a separate grant type from Policy Editor Grants, scoped by which groups the delegate can manage rather than by resource tags.
