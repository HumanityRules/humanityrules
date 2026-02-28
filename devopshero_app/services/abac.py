"""
ABAC (Attribute-Based Access Control) policy evaluation engine.

Evaluates access by matching identity attributes against resource tags via policies.
"""

from typing import TypeVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet

from devopshero_app.models import (
    App,
    AppPermissionRequest,
    Environment,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    ResourceTag,
    User,
    Workspace,
)

# ---------------------------------------------------------------------------
# Action hierarchy: admin actions imply lower-level actions
# ---------------------------------------------------------------------------

ACTION_HIERARCHY: dict[str, set[str]] = {
    "workspace:admin": {"workspace:view", "workspace:edit"},
    "environment:admin": {"environment:view", "environment:deploy", "environment:approve"},
}


# ---------------------------------------------------------------------------
# Effective attributes
# ---------------------------------------------------------------------------

def get_effective_attributes(organization: Organization, user: User) -> list[tuple[str, str, str]]:
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

def _assert_resource_belongs_to_org(organization: Organization, resource: App | Environment | Workspace, resource_type: str) -> None:
    """Raise if resource does not belong to the given organization."""
    if resource_type in ("workspace", "app"):
        actual_org_id = resource.organization_id
    elif resource_type == "environment":
        actual_org_id = resource.aws_account.organization_id
    else:
        return
    if actual_org_id != organization.pk:
        raise ValueError(
            f"Resource {resource_type} {resource.pk!r} belongs to organization "
            f"{actual_org_id!r}, not {organization.slug!r} ({organization.pk!r}). "
            f"Callers must pass the resource's own organization."
        )


def get_effective_tags(organization: Organization, resource: App | Environment | Workspace, resource_type: str) -> list[tuple[str, str, str]]:
    """
    Return list of (key, value, source) tuples for a resource.
    Apps inherit workspace tags (source="inherited:<WorkspaceName>").
    """
    _assert_resource_belongs_to_org(organization, resource, resource_type)
    tags = []

    if resource_type == "app":
        for t in ResourceTag.objects.filter(organization=organization, app=resource):
            tags.append((t.key, t.value, "direct"))
        for t in ResourceTag.objects.filter(organization=organization, workspace=resource.workspace):
            tags.append((t.key, t.value, f"inherited:{resource.workspace.name}"))
    elif resource_type == "workspace":
        for t in ResourceTag.objects.filter(organization=organization, workspace=resource):
            tags.append((t.key, t.value, "direct"))
    elif resource_type == "environment":
        for t in ResourceTag.objects.filter(organization=organization, environment=resource):
            tags.append((t.key, t.value, "direct"))

    return tags


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------

def validate_policy_conditions(identity_conditions: list[dict], resource_conditions: list[dict]) -> None:
    """Raise ValidationError if wildcard is mixed with other conditions."""
    _validate_no_mixed_wildcard(identity_conditions, "Identity")
    _validate_no_mixed_wildcard(resource_conditions, "Resource")


def _validate_no_mixed_wildcard(conditions: list[dict], label: str) -> None:
    """Raise ValidationError if a wildcard entry coexists with non-wildcard entries."""
    if len(conditions) <= 1:
        return
    has_wildcard = any(c.get("key") == "*" and c.get("value") == "*" for c in conditions)
    if has_wildcard:
        raise ValidationError(
            f"{label} conditions: wildcard (*) cannot be combined with other conditions. "
            f"Use wildcard as the sole condition, or remove it.",
        )


def _is_wildcard(conditions: list[dict[str, str]]) -> bool:
    """Check if conditions list is a wildcard (matches everything). Only valid as sole entry."""
    return len(conditions) == 1 and conditions[0].get("key") == "*" and conditions[0].get("value") == "*"


def _conditions_match(conditions: list[dict[str, str]], attribute_set: set[tuple[str, str]]) -> bool:
    """Check if all conditions are present in the attribute set. Wildcard always matches."""
    if _is_wildcard(conditions):
        return True
    return all(
        (c.get("key"), c.get("value")) in attribute_set
        for c in conditions
    )


def evaluate_policies(
    organization: Organization,
    user: User,
    resource: App | Environment | Workspace,
    resource_type: str,
) -> set[str]:
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

    effective_tags = get_effective_tags(organization, resource, resource_type)
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


def evaluate_policies_unscoped(organization: Organization, user: User, resource_type: str) -> set[str]:
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


def check_action(
    organization: Organization,
    user: User,
    resource: App | Environment | Workspace,
    resource_type: str,
    action: str,
) -> bool:
    """Convenience: returns True if user has action on resource."""
    allowed = evaluate_policies(organization, user, resource, resource_type)
    return action in allowed


ResourceTypeT = TypeVar("ResourceTypeT", App, Environment, Workspace)


def _assert_queryset_org_scope(queryset: QuerySet[ResourceTypeT], organization: Organization, resource_type: str) -> None:
    """Raise if queryset contains resources outside the given organization."""
    if resource_type in ("workspace", "app"):
        foreign_count = queryset.exclude(organization=organization).count()
    elif resource_type == "environment":
        foreign_count = queryset.exclude(aws_account__organization=organization).count()
    else:
        return
    if foreign_count > 0:
        raise ValueError(
            f"filter_permitted_resources received {foreign_count} resource(s) "
            f"outside organization {organization.slug!r}. "
            f"Callers must pre-scope querysets to a single organization."
        )


def filter_permitted_resources(
    organization: Organization,
    user: User,
    queryset: QuerySet[ResourceTypeT],
    resource_type: str,
    action: str,
) -> QuerySet[ResourceTypeT]:
    """
    Given a queryset of resources, return only those the user has `action` on.
    Loads all matching policies once, then evaluates per-resource.
    """
    _assert_queryset_org_scope(queryset, organization, resource_type)
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
        has_scoped_denials = any(
            not _is_wildcard(p.resource_conditions or [])
            and any(a.startswith("!") for a in (p.actions or []))
            for p in matching_policies
        )
        if action in (expanded - denials) and not has_scoped_denials:
            return queryset  # All resources permitted via wildcard

    # Per-resource evaluation for non-wildcard policies
    permitted_ids = []
    for resource in queryset:
        tags = get_effective_tags(organization, resource, resource_type)
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


def is_org_admin(organization: Organization, user: User) -> bool:
    """Check if user has org-role=admin attribute (direct or group-inherited)."""
    attrs = get_effective_attributes(organization, user)
    return any(k == "org-role" and v == "admin" for k, v, _ in attrs)


def filter_visible_app_permission_requests(
    organization: Organization,
    user: User,
    queryset: QuerySet[AppPermissionRequest],
) -> QuerySet[AppPermissionRequest]:
    """Filter app permission requests to those the user should see: own, approvable, or all (org-admin)."""
    if not OrganizationMembership.objects.filter(organization=organization, user=user).exists():
        raise ValueError(
            f"filter_visible_app_permission_requests called for user {user.email!r} "
            f"who is not a member of organization {organization.slug!r}."
        )

    if is_org_admin(organization, user):
        return queryset

    approvable_envs = filter_permitted_resources(
        organization=organization,
        user=user,
        queryset=Environment.objects.filter(aws_account__organization=organization),
        resource_type="environment",
        action="environment:approve",
    )

    return queryset.filter(
        models.Q(created_by=user) | models.Q(environment__in=approvable_envs)
    )


# ---------------------------------------------------------------------------
# Bootstrapping
# ---------------------------------------------------------------------------

def bootstrap_organization(organization: Organization, admin_user: User) -> None:
    """
    Create seed ABAC data for a new organization:
    1. IdentityAttribute org-role=admin on admin_user
    2. Nine seed policies for admin/member/viewer org-roles
    """
    IdentityAttribute.objects.get_or_create(
        organization=organization,
        user=admin_user,
        key="org-role",
        value="admin",
    )

    seed_policies = [
        # Admin: full control over everything
        {
            "name": "Org admins: full workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "actions": ["workspace:admin"],
        },
        {
            "name": "Org admins: full environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "actions": ["environment:admin"],
        },
        {
            "name": "Org admins: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "actions": ["app:use"],
        },
        # Member: view/edit workspaces, view/deploy environments, use apps
        {
            "name": "Org members: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["workspace:view", "workspace:edit"],
        },
        {
            "name": "Org members: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["environment:view", "environment:deploy"],
        },
        {
            "name": "Org members: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "actions": ["app:use"],
        },
        # Viewer: read-only platform access, use apps
        {
            "name": "Org viewers: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["workspace:view"],
        },
        {
            "name": "Org viewers: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["environment:view"],
        },
        {
            "name": "Org viewers: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "actions": ["app:use"],
        },
    ]

    for seed in seed_policies:
        Policy.objects.get_or_create(
            organization=organization,
            name=seed["name"],
            defaults={
                "resource_type": seed["resource_type"],
                "identity_conditions": seed["identity_conditions"],
                "resource_conditions": [{"key": "*", "value": "*"}],
                "actions": seed["actions"],
                "is_system": True,
            },
        )


def create_default_app_policy(app: App) -> None:
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


def create_default_workspace_tag(workspace: Workspace) -> None:
    """Create a workspace-name tag when a new Workspace is created."""
    ResourceTag.objects.get_or_create(
        organization=workspace.organization,
        resource_type="workspace",
        workspace=workspace,
        key="workspace-name",
        value=workspace.slug,
    )


def create_default_environment_tag(environment: Environment) -> None:
    """Create an environment-name tag when a new Environment is created."""
    ResourceTag.objects.get_or_create(
        organization=environment.aws_account.organization,
        resource_type="environment",
        environment=environment,
        key="environment-name",
        value=environment.slug,
    )


# ---------------------------------------------------------------------------
# Suggestion palette for tag/attribute input forms
# ---------------------------------------------------------------------------

SUGGESTED_KEYS = {"org-role", "authenticated", "team", "role"}
SUGGESTED_VALUES = {"admin", "member", "viewer", "true"}


def get_identity_attribute_suggestion_keys(org: Organization) -> list[str]:
    """Suggestion keys for identity attribute forms (people, groups)."""
    attr_keys = set(IdentityAttribute.objects.filter(organization=org).values_list("key", flat=True).distinct())
    group_attr_keys = set(GroupAttribute.objects.filter(group__organization=org).values_list("key", flat=True).distinct())
    return sorted(attr_keys | group_attr_keys | SUGGESTED_KEYS)


def get_identity_attribute_suggestion_values(org: Organization) -> list[str]:
    """Suggestion values for identity attribute forms (people, groups)."""
    attr_vals = set(IdentityAttribute.objects.filter(organization=org).values_list("value", flat=True).distinct())
    group_attr_vals = set(GroupAttribute.objects.filter(group__organization=org).values_list("value", flat=True).distinct())
    return sorted(attr_vals | group_attr_vals | SUGGESTED_VALUES)


def get_resource_tag_suggestion_keys(org: Organization) -> list[str]:
    """Suggestion keys for resource tag forms (workspaces, apps, environments)."""
    tag_keys = set(ResourceTag.objects.filter(organization=org).values_list("key", flat=True).distinct())
    return sorted(tag_keys | SUGGESTED_KEYS)


def get_resource_tag_suggestion_values(org: Organization) -> list[str]:
    """Suggestion values for resource tag forms (workspaces, apps, environments)."""
    tag_vals = set(ResourceTag.objects.filter(organization=org).values_list("value", flat=True).distinct())
    return sorted(tag_vals | SUGGESTED_VALUES)


