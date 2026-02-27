"""
ABAC (Attribute-Based Access Control) policy evaluation engine.

Evaluates access by matching identity attributes against resource tags via policies.
"""

from devopshero_app.models import (
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Policy,
    ResourceTag,
)

# ---------------------------------------------------------------------------
# Action hierarchy: admin actions imply lower-level actions
# ---------------------------------------------------------------------------

ACTION_HIERARCHY = {
    "workspace:admin": {"workspace:view", "workspace:edit"},
    "environment:admin": {"environment:view", "environment:deploy", "environment:approve"},
}


# ---------------------------------------------------------------------------
# Effective attributes
# ---------------------------------------------------------------------------

def get_effective_attributes(organization, user):
    """
    Return list of (key, value, source) tuples for a user.
    Sources: "system", "direct", "group:<GroupName>".
    """
    attrs = [("authenticated", "true", "system")]

    # Direct attributes
    for ia in IdentityAttribute.objects.filter(organization=organization, user=user):
        attrs.append((ia.key, ia.value, "direct"))

    # Group-inherited attributes
    memberships = GroupMembership.objects.filter(
        group__organization=organization, user=user,
    ).select_related("group")
    group_ids = []
    group_names = {}
    for m in memberships:
        group_ids.append(m.group_id)
        group_names[m.group_id] = m.group.name

    if group_ids:
        for ga in GroupAttribute.objects.filter(group_id__in=group_ids):
            attrs.append((ga.key, ga.value, f"group:{group_names[ga.group_id]}"))

    return attrs


# ---------------------------------------------------------------------------
# Effective tags
# ---------------------------------------------------------------------------

def get_effective_tags(resource, resource_type):
    """
    Return list of (key, value, source) tuples for a resource.
    Apps inherit workspace tags (source="inherited:<WorkspaceName>").
    """
    tags = []

    if resource_type == "app":
        # Direct app tags
        for t in ResourceTag.objects.filter(app=resource):
            tags.append((t.key, t.value, "direct"))
        # Inherited workspace tags
        for t in ResourceTag.objects.filter(workspace=resource.workspace):
            tags.append((t.key, t.value, f"inherited:{resource.workspace.name}"))
    elif resource_type == "workspace":
        for t in ResourceTag.objects.filter(workspace=resource):
            tags.append((t.key, t.value, "direct"))
    elif resource_type == "environment":
        for t in ResourceTag.objects.filter(environment=resource):
            tags.append((t.key, t.value, "direct"))

    return tags


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------

def _is_wildcard(conditions):
    """Check if conditions list is a wildcard (matches everything)."""
    return any(c.get("key") == "*" and c.get("value") == "*" for c in conditions)


def _conditions_match(conditions, attribute_set):
    """Check if all conditions are present in the attribute set. Wildcard always matches."""
    if _is_wildcard(conditions):
        return True
    return all(
        (c.get("key"), c.get("value")) in attribute_set
        for c in conditions
    )


def evaluate_policies(organization, user, resource, resource_type):
    """
    Core ABAC engine. Returns set of allowed action strings.

    Algorithm:
    1. Load all org policies for the resource_type
    2. Compute effective attributes and tags as sets
    3. For each policy: check identity + resource condition match
    4. Collect grants and denials
    5. Expand grant hierarchy
    6. Remove denied actions (deny-overrides)
    """
    policies = Policy.objects.filter(organization=organization, resource_type=resource_type)

    effective_attrs = get_effective_attributes(organization, user)
    attr_set = {(k, v) for k, v, _ in effective_attrs}

    effective_tags = get_effective_tags(resource, resource_type)
    tag_set = {(k, v) for k, v, _ in effective_tags}

    grants = set()
    denials = set()

    for policy in policies:
        identity_match = _conditions_match(policy.identity_conditions or [], attr_set)
        resource_match = _conditions_match(policy.resource_conditions or [], tag_set)

        if identity_match and resource_match:
            for action in (policy.actions or []):
                if action.startswith("!"):
                    denials.add(action[1:])
                else:
                    grants.add(action)

    # Expand hierarchy
    expanded = set()
    for action in grants:
        expanded.add(action)
        if action in ACTION_HIERARCHY:
            expanded.update(ACTION_HIERARCHY[action])

    # Deny-overrides
    return expanded - denials


def evaluate_policies_unscoped(organization, user, resource_type):
    """
    Evaluate with empty tag set — only wildcard-resource policies match.
    Used for "can user create new workspaces?" checks.
    """
    policies = Policy.objects.filter(organization=organization, resource_type=resource_type)

    effective_attrs = get_effective_attributes(organization, user)
    attr_set = {(k, v) for k, v, _ in effective_attrs}

    grants = set()
    denials = set()

    for policy in policies:
        identity_match = _conditions_match(policy.identity_conditions or [], attr_set)
        resource_match = _is_wildcard(policy.resource_conditions or [])

        if identity_match and resource_match:
            for action in (policy.actions or []):
                if action.startswith("!"):
                    denials.add(action[1:])
                else:
                    grants.add(action)

    expanded = set()
    for action in grants:
        expanded.add(action)
        if action in ACTION_HIERARCHY:
            expanded.update(ACTION_HIERARCHY[action])

    return expanded - denials


def check_action(organization, user, resource, resource_type, action):
    """Convenience: returns True if user has action on resource."""
    allowed = evaluate_policies(organization, user, resource, resource_type)
    return action in allowed


def filter_permitted_resources(organization, user, queryset, resource_type, action):
    """
    Given a queryset of resources, return only those the user has `action` on.
    Loads all matching policies once, then evaluates per-resource.
    """
    policies = list(Policy.objects.filter(organization=organization, resource_type=resource_type))

    effective_attrs = get_effective_attributes(organization, user)
    attr_set = {(k, v) for k, v, _ in effective_attrs}

    # Pre-filter policies: only keep those whose identity conditions match
    matching_policies = []
    for policy in policies:
        if _conditions_match(policy.identity_conditions or [], attr_set):
            matching_policies.append(policy)

    if not matching_policies:
        return queryset.none()

    # Check if any matching policy has wildcard resource conditions
    # If so, ALL resources match that policy
    has_wildcard = any(
        _is_wildcard(p.resource_conditions or [])
        for p in matching_policies
    )
    if has_wildcard:
        # Collect grants/denials from wildcard policies
        grants = set()
        denials = set()
        for p in matching_policies:
            if _is_wildcard(p.resource_conditions or []):
                for a in (p.actions or []):
                    if a.startswith("!"):
                        denials.add(a[1:])
                    else:
                        grants.add(a)
        expanded = set()
        for a in grants:
            expanded.add(a)
            if a in ACTION_HIERARCHY:
                expanded.update(ACTION_HIERARCHY[a])
        if action in (expanded - denials):
            return queryset  # All resources permitted via wildcard

    # Per-resource evaluation for non-wildcard policies
    permitted_ids = []
    for resource in queryset:
        tags = get_effective_tags(resource, resource_type)
        tag_set = {(k, v) for k, v, _ in tags}

        grants = set()
        denials = set()
        for policy in matching_policies:
            if _conditions_match(policy.resource_conditions or [], tag_set):
                for a in (policy.actions or []):
                    if a.startswith("!"):
                        denials.add(a[1:])
                    else:
                        grants.add(a)

        expanded = set()
        for a in grants:
            expanded.add(a)
            if a in ACTION_HIERARCHY:
                expanded.update(ACTION_HIERARCHY[a])

        if action in (expanded - denials):
            permitted_ids.append(resource.pk)

    return queryset.filter(pk__in=permitted_ids)


def is_org_admin(organization, user):
    """Check if user has org-role=admin attribute (direct or group-inherited)."""
    attrs = get_effective_attributes(organization, user)
    return any(k == "org-role" and v == "admin" for k, v, _ in attrs)


# ---------------------------------------------------------------------------
# Bootstrapping
# ---------------------------------------------------------------------------

def bootstrap_organization(organization, admin_user):
    """
    Create seed ABAC data for a new organization:
    1. IdentityAttribute org-role=admin on admin_user
    2. Three seed policies (workspace:admin, environment:admin, app:use) for org admins
    """
    IdentityAttribute.objects.get_or_create(
        organization=organization,
        user=admin_user,
        key="org-role",
        value="admin",
    )

    seed_policies = [
        {
            "name": "Org admins: full workspace access",
            "resource_type": "workspace",
            "actions": ["workspace:admin"],
        },
        {
            "name": "Org admins: full environment access",
            "resource_type": "environment",
            "actions": ["environment:admin"],
        },
        {
            "name": "Org admins: app usage",
            "resource_type": "app",
            "actions": ["app:use"],
        },
    ]

    for seed in seed_policies:
        Policy.objects.get_or_create(
            organization=organization,
            name=seed["name"],
            defaults={
                "resource_type": seed["resource_type"],
                "identity_conditions": [{"key": "org-role", "value": "admin"}],
                "resource_conditions": [{"key": "*", "value": "*"}],
                "actions": seed["actions"],
                "is_system": True,
            },
        )


def create_default_app_policy(app):
    """
    Create a default app:use policy and app-name tag when a new App is created.
    Called from App post_save signal.
    """
    org = app.organization

    # Create app-name tag
    ResourceTag.objects.get_or_create(
        organization=org,
        resource_type="app",
        app=app,
        key="app-name",
        value=app.slug,
    )

    # Create open-access policy for this app
    Policy.objects.get_or_create(
        organization=org,
        name=f"Default: {app.name} open access",
        defaults={
            "resource_type": "app",
            "identity_conditions": [{"key": "*", "value": "*"}],
            "resource_conditions": [{"key": "app-name", "value": app.slug}],
            "actions": ["app:use"],
            "is_system": True,
        },
    )
