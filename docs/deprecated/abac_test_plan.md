# ABAC Test Plan

Test plan for the ABAC (Attribute-Based Access Control) authorization system. This is the first test suite in the project — the ABAC engine is security-critical and benefits most from automated testing.

Reference: `docs/authorization_design_abac.md` for the full design, `devopshero_app/services/abac.py` for the evaluation engine, `devopshero_app/views/abac_view_checks.py` for the view enforcement helpers.


## 1. Policy Evaluation Engine

Unit tests for `devopshero_app/services/abac.py`. Pure logic with no HTTP concerns — fast and high-value.

### 1.1 Effective Attributes (`get_effective_attributes`)

- User with no direct or group attributes gets only `("authenticated", "true", "system")`
- User with direct attributes sees them with source `"direct"`
- User in a group inherits that group's attributes with source `"group:<GroupName>"`
- User in multiple groups gets the union of all group attributes
- User with the same key from both direct and group sources sees both entries (they are not deduplicated — both participate in policy evaluation)

### 1.2 Effective Tags (`get_effective_tags`)

- App with direct tags only — source is `"direct"`
- App inherits parent workspace tags — source is `"inherited:<WorkspaceName>"`
- App with both direct and inherited tags returns both
- Workspace tags are always source `"direct"` (no inheritance)
- Environment tags are always source `"direct"` (environments do not inherit from anything)

### 1.3 Condition Matching (`_conditions_match`)

- Wildcard `[{"key": "*", "value": "*"}]` matches any attribute set (including empty)
- Single condition present in the set returns True
- Single condition absent from the set returns False
- Multiple conditions (AND semantics): all present returns True
- Multiple conditions: one missing returns False
- Empty conditions list returns True (vacuously true — all zero conditions are met)

### 1.4 Policy Evaluation (`evaluate_policies`)

- Single policy with matching identity + resource conditions grants its actions
- Policy with non-matching identity conditions is skipped
- Policy with non-matching resource conditions is skipped
- Multiple policies — grants are the union of all matching policies' actions
- Action hierarchy expansion: granting `workspace:admin` also yields `workspace:view` and `workspace:edit`
- Action hierarchy expansion: granting `environment:admin` also yields `environment:view`, `environment:deploy`, `environment:approve`
- Deny-overrides: if one policy grants `workspace:view` and another denies `!workspace:view`, the action is denied
- Deny on an expanded action: granting `workspace:admin` then denying `!workspace:view` results in `workspace:admin` + `workspace:edit` but not `workspace:view`
- No matching policies returns empty set (deny-by-default)
- Policy with wildcard identity condition (`*`) matches any authenticated user
- Policy with wildcard resource condition (`*`) matches any resource regardless of tags

### 1.5 Unscoped Evaluation (`evaluate_policies_unscoped`)

- Only wildcard-resource policies match (tag set is empty, so specific resource conditions can't match)
- Non-wildcard resource policies are ignored
- Used for creation checks ("can this user create new workspaces?")
- Action hierarchy expansion still applies

### 1.6 Resource Filtering (`filter_permitted_resources`)

- User with a wildcard-resource policy gets the entire queryset back (optimization shortcut)
- User with tag-scoped policies only gets resources whose tags match
- User with no matching identity conditions gets an empty queryset
- Mix of wildcard and non-wildcard policies — wildcard grants all, non-wildcard grants additional specific resources
- Deny policy removes specific resources from the result set

### 1.7 Org Admin Check (`is_org_admin`)

- User with `org-role=admin` as direct attribute returns True
- User with `org-role=admin` inherited from a group returns True
- User without `org-role=admin` returns False


## 2. View-Level Endpoint Access

Integration tests using Django test client. Set up different users with different attribute profiles, hit every ABAC-protected endpoint, assert 200 vs 403.

### 2.1 Workspace Endpoints

- Org admin can list, view, create, edit, and manage tags on workspaces
- User with `workspace:view` on a specific workspace can view it but gets 403 on edit and tag management
- User with `workspace:edit` can edit but gets 403 on tag management (`workspace:admin` required)
- User with no workspace grants gets 403 on workspace detail
- Workspace list (`filter_permitted_resources`) only returns workspaces the user has `workspace:view` on

### 2.2 Environment Endpoints

- Org admin can list, view, and manage tags on environments
- User with `environment:view` can view but gets 403 on tag management
- User with `environment:admin` can manage tags
- Environment list only returns environments the user has `environment:view` on

### 2.3 App Endpoints

- Viewing an app requires `workspace:view` on the parent workspace (not the app itself)
- Editing app settings requires `workspace:edit` on the parent workspace
- Tag management on an app requires `workspace:admin` on the parent workspace
- User with no workspace grants gets 403 on app detail

### 2.4 Security Settings Endpoints

- Org admin can access all people/groups/policies tabs (200)
- Non-org-admin gets 403 on every security settings endpoint
- Org admin can create/edit/delete groups, attributes, and policies
- Non-admin user gets 403 on all mutation endpoints (attribute add/remove, group create/delete, policy create/delete)

### 2.5 Permissions Editor

- Approving an AppPermissionRequest requires `environment:approve` on the target environment
- User without `environment:approve` gets 403 on the approval endpoint


## 3. Bootstrapping & Defaults

### 3.1 Organization Bootstrap (`bootstrap_organization`)

- Creates `org-role=admin` IdentityAttribute on the admin user
- Creates 3 seed policies (workspace:admin, environment:admin, app:use for org admins)
- Is idempotent — running twice does not duplicate attributes or policies

### 3.2 App Default Policy (`create_default_app_policy`)

- Creates an `app-name=<slug>` ResourceTag on the app
- Creates a wildcard-identity `app:use` policy scoped to that tag
- Is idempotent

### 3.3 Auto-Tags via Signals

- Creating a Workspace produces a `workspace-name=<slug>` ResourceTag
- Creating an Environment produces an `environment-name=<slug>` ResourceTag


## 4. Cross-Organization Isolation

Security-critical: policies and attributes from one org must never leak into another.

- User in org A with admin attributes cannot access org B's workspaces
- Policies created in org A do not influence evaluation in org B
- `filter_permitted_resources` scoped to org A returns nothing from org B even if the user has matching attributes in org A
- Attributes are org-scoped: same user with `org-role=admin` in org A does not have that attribute in org B


## 5. Realistic Scenario Tests

End-to-end scenarios replicating real usage patterns from the design doc.

### 5.1 Team Onboarding

Replicate the "onboarding a new team" flow from `docs/authorization_design_abac.md`:

1. Create org, bootstrap, create a group with `team=data-platform`
2. Add users to the group (they inherit the attribute)
3. Create workspace tagged `domain=data-platform`
4. Create policy: IF identity `team=data-platform` AND resource `domain=data-platform` THEN `workspace:view`, `workspace:edit`
5. Verify group members can access the workspace
6. Verify a user outside the group cannot access the workspace
7. Add a new user to the group — they gain access without any policy change

### 5.2 Deny-Override Scenario

1. Grant `environment:deploy` to users with `job-function=developer` on environments tagged `tier=staging`
2. Create a deny policy: `employment-type=contractor` on `tier=staging` denies `!environment:deploy`
3. A developer-contractor (has both attributes) is denied deploy despite the grant
4. A developer-employee (has `job-function=developer` but not `employment-type=contractor`) can deploy normally

### 5.3 Tag Inheritance Consistency

1. Create a workspace with tag `domain=finance`
2. Create an app in that workspace — app inherits `domain=finance`
3. Create a policy granting `app:use` when resource has `domain=finance`
4. Verify the app is accessible
5. Remove the `domain=finance` tag from the workspace
6. Verify the app is no longer accessible via that policy


## 6. Edge Cases

- Policy with empty `identity_conditions` (null or `[]`)
- Policy with empty `resource_conditions` (null or `[]`)
- Policy with empty `actions` (null or `[]`) — should grant nothing
- Multiple values for the same attribute key on a single user (`team=frontend` AND `team=backend`) — a policy requiring `team=frontend` matches
- User with attributes but no policies exist in the org — denied everything
- Resource with tags but no policies exist in the org — denied everything
- Policy with only deny actions and no grants — user ends up with empty grant set (deny-by-default still applies)
