"""
ABAC (Attribute-Based Access Control) policy evaluation engine.

Evaluates access by matching identity attributes against resource tags via policies.
"""

import re
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

# A condition value may be a literal string or a cross-side reference of the
# form "$identity.<key>" (valid on the resource-conditions side) or
# "$resource.<key>" (valid on the identity-conditions side). The reference is
# resolved at evaluation time against the opposite side's (key -> {values}) map.
_REFERENCE_PATTERN = re.compile(r"^\$(identity|resource)\.([a-zA-Z0-9_\-]+)$")

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
    """Raise ValidationError if conditions are malformed."""
    _validate_no_mixed_wildcard(identity_conditions, "Identity")
    _validate_no_mixed_wildcard(resource_conditions, "Resource")
    _validate_references(identity_conditions, allowed_side="resource", label="Identity")
    _validate_references(resource_conditions, allowed_side="identity", label="Resource")


def _validate_references(conditions: list[dict], allowed_side: str, label: str) -> None:
    """Reject references on the wrong side or references on the condition's key field."""
    for c in conditions:
        key = c.get("key")
        value = c.get("value")
        if isinstance(key, str) and _parse_reference(key) is not None:
            raise ValidationError(
                f"{label} conditions: references are only allowed in the 'value' field, "
                f"not in 'key'. Got key={key!r}.",
            )
        ref = _parse_reference(value) if isinstance(value, str) else None
        if ref is None:
            continue
        ref_side, _ = ref
        if ref_side != allowed_side:
            raise ValidationError(
                f"{label} conditions: value reference {value!r} points at the wrong side. "
                f"Only $({allowed_side}).<key> is valid here.",
            )


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


def _parse_reference(value: str) -> tuple[str, str] | None:
    """Parse a "$identity.<key>" / "$resource.<key>" reference. Returns (side, key) or None."""
    if not isinstance(value, str):
        return None
    m = _REFERENCE_PATTERN.match(value)
    if m is None:
        return None
    return (m.group(1), m.group(2))


def _conditions_match(
    conditions: list[dict[str, str]],
    self_side: set[tuple[str, str]],
    other_side_label: str,
    other_side_map: dict[str, set[str]] | None,
) -> bool:
    """Check if all conditions match. Wildcard matches unconditionally.

    A clause's value may be a cross-side reference like "$resource.<key>" (on
    identity conditions) or "$identity.<key>" (on resource conditions). The
    reference is resolved against *other_side_map*: the clause matches iff
    ``(clause.key, v)`` is in *self_side* for some value v the referenced key
    carries on the other side.

    *other_side_label* is the side references are allowed to point at ("resource"
    when evaluating identity conditions, "identity" when evaluating resource
    conditions). References with the wrong side, and references at all when
    *other_side_map* is None, cause the clause to fail closed.
    """
    if _is_wildcard(conditions):
        return True
    for c in conditions:
        key = c.get("key")
        value = c.get("value")
        ref = _parse_reference(value) if isinstance(value, str) else None
        if ref is None:
            if (key, value) not in self_side:
                return False
            continue
        ref_side, ref_key = ref
        if ref_side != other_side_label or other_side_map is None:
            return False
        candidate_values = other_side_map.get(ref_key)
        if not candidate_values:
            return False
        if not any((key, v) in self_side for v in candidate_values):
            return False
    return True


def _build_side_map(pairs: set[tuple[str, str]]) -> dict[str, set[str]]:
    """Group (key, value) pairs into a {key: {values}} map for reference resolution."""
    result: dict[str, set[str]] = {}
    for k, v in pairs:
        result.setdefault(k, set()).add(v)
    return result


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
    attr_map = _build_side_map(attr_set)

    effective_tags = get_effective_tags(organization, resource, resource_type)
    tag_set = {(k, v) for k, v, _ in effective_tags}
    tag_map = _build_side_map(tag_set)

    grants = set()
    denials = set()

    for policy in policies:
        identity_match = _conditions_match(
            conditions=policy.identity_conditions or [],
            self_side=attr_set,
            other_side_label="resource",
            other_side_map=tag_map,
        )
        resource_match = _conditions_match(
            conditions=policy.resource_conditions or [],
            self_side=tag_set,
            other_side_label="identity",
            other_side_map=attr_map,
        )

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
    attr_map = _build_side_map(attr_set)

    grants = set()
    denials = set()

    for policy in policies:
        # Unscoped evaluation has no resource in hand; $resource.* references
        # on the identity side cannot resolve, so pass no other_side_map.
        identity_match = _conditions_match(
            conditions=policy.identity_conditions or [],
            self_side=attr_set,
            other_side_label="resource",
            other_side_map=None,
        )
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
    attr_map = _build_side_map(attr_set)

    # Pre-filter policies whose identity conditions can be decided without a
    # resource in hand. Policies with $resource.* references must defer to
    # per-resource evaluation regardless of the attribute-only outcome.
    matching_policies = []
    for policy in policies:
        conds = policy.identity_conditions or []
        has_resource_ref = any(
            _parse_reference(c.get("value")) is not None
            and _parse_reference(c.get("value"))[0] == "resource"
            for c in conds
        )
        if has_resource_ref:
            matching_policies.append(policy)
            continue
        if _conditions_match(
            conditions=conds,
            self_side=attr_set,
            other_side_label="resource",
            other_side_map=None,
        ):
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
        # Collect grants/denials from wildcard policies whose identity
        # conditions do not depend on the resource (those need per-resource
        # evaluation to decide).
        grants = set()
        denials = set()
        for p in matching_policies:
            if not _is_wildcard(p.resource_conditions or []):
                continue
            id_conds = p.identity_conditions or []
            if any(
                _parse_reference(c.get("value")) is not None
                and _parse_reference(c.get("value"))[0] == "resource"
                for c in id_conds
            ):
                continue
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
        tag_map = _build_side_map(tag_set)

        grants = set()
        denials = set()
        for policy in matching_policies:
            identity_match = _conditions_match(
                conditions=policy.identity_conditions or [],
                self_side=attr_set,
                other_side_label="resource",
                other_side_map=tag_map,
            )
            if not identity_match:
                continue
            if not _conditions_match(
                conditions=policy.resource_conditions or [],
                self_side=tag_set,
                other_side_label="identity",
                other_side_map=attr_map,
            ):
                continue
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
    2. Wildcard-resource seed policies for admin/member/viewer org-roles
    3. Personal-Assistant owner policy (self-referential, global per org)

    The member and viewer roles intentionally do NOT get a wildcard "app:use"
    grant. Per-app access is governed by each app's own default policy (see
    create_default_app_policy), which is created when the App is saved and
    which the deploy flow may suppress for restricted apps like PAs.
    """
    IdentityAttribute.objects.get_or_create(
        organization=organization,
        user=admin_user,
        key="org-role",
        value="admin",
    )

    seed_policies = [
        # Admin: full control over everything, including every app.
        {
            "name": "Org admins: full workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["workspace:admin"],
        },
        {
            "name": "Org admins: full environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["environment:admin"],
        },
        {
            "name": "Org admins: app usage",
            "resource_type": "app",
            "identity_conditions": [{"key": "org-role", "value": "admin"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["app:use"],
        },
        # Member: view/edit workspaces, view/deploy environments. App access is
        # governed per-app (see create_default_app_policy) so member-only apps
        # and owner-only apps (Personal Assistants) can coexist.
        {
            "name": "Org members: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["workspace:view", "workspace:edit"],
        },
        {
            "name": "Org members: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "member"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["environment:view", "environment:deploy"],
        },
        # Viewer: read-only platform access. Same reasoning as member for apps.
        {
            "name": "Org viewers: workspace access",
            "resource_type": "workspace",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["workspace:view"],
        },
        {
            "name": "Org viewers: environment access",
            "resource_type": "environment",
            "identity_conditions": [{"key": "org-role", "value": "viewer"}],
            "resource_conditions": [{"key": "*", "value": "*"}],
            "actions": ["environment:view"],
        },
        # Personal Assistant owner: one global self-referential policy replaces
        # N per-PA policies. Only the user whose username matches the app's
        # owner tag gets app:use on apps tagged app-type=personal-assistant.
        {
            "name": "Personal Assistant: owner access",
            "resource_type": "app",
            "identity_conditions": [{"key": "username", "value": "$resource.owner"}],
            "resource_conditions": [{"key": "app-type", "value": "personal-assistant"}],
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
                "resource_conditions": seed["resource_conditions"],
                "actions": seed["actions"],
                "is_system": True,
            },
        )


def assign_default_org_role(organization: Organization, user: User) -> None:
    """Assign the organization's default org-role as an IdentityAttribute on a new member."""
    IdentityAttribute.objects.get_or_create(
        organization=organization,
        user=user,
        key="org-role",
        value=organization.default_org_role,
    )


def create_default_app_policy(app: App) -> None:
    """
    Create a default app:use policy and app-name tag when a new App is created.
    Called from App post_save signal.

    Apps whose source template opts into the sidecar proxy (e.g. Personal
    Assistants) are NOT given the open-access default — their access is
    governed by purpose-built policies (e.g. the global PA owner policy).
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

    if app.source_template and app.source_template.sidecar_enabled:
        return

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

SEED_ORG_ROLES = {"admin", "member", "viewer"}


def get_known_org_role_values(organization: Organization) -> list[str]:
    """Return all org-role values known for this organization (from attributes + seeds), sorted."""
    db_values = set(
        IdentityAttribute.objects.filter(
            organization=organization, key="org-role",
        ).values_list("value", flat=True).distinct()
    ) | set(
        GroupAttribute.objects.filter(
            group__organization=organization, key="org-role",
        ).values_list("value", flat=True).distinct()
    )
    return sorted(db_values | SEED_ORG_ROLES)


# Seed suggestions: dict of key → set of suggested values.
# Keys with empty sets still appear as key suggestions (just no value dropdown).

IDENTITY_SUGGESTIONS: dict[str, set[str]] = {
    "org-role": {"admin", "member", "viewer"},
    "team": set(),
    "role": set(),
}

RESOURCE_SUGGESTIONS: dict[str, dict[str, set[str]]] = {
    "workspace": {
        "project": set(),
        "team": set(),
    },
    "environment": {
        "stage": {"production", "staging", "development"},
    },
    "app": {},
}

# System attributes — not manually assignable, but valid in policy identity conditions
SYSTEM_ATTRIBUTES: dict[str, set[str]] = {
    "authenticated": {"true"},
}


def _expand_suggestions(suggestions: dict[str, set[str]]) -> tuple[set[str], set[tuple[str, str]]]:
    """Expand a {key: {values}} dict into (keys, pairs) sets."""
    keys = set(suggestions.keys())
    pairs = {(k, v) for k, vals in suggestions.items() for v in vals}
    return keys, pairs


def get_identity_attribute_suggestions(
    org: Organization, include_system: bool = False,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Returns (sorted_keys, sorted_key_value_pairs) for identity attribute suggestions.

    Set include_system=True for policy identity conditions (includes system
    attributes like ``authenticated``).  Leave False for people/group attribute
    forms where only manually-assignable attributes should be suggested.
    """
    db_pairs = set(
        IdentityAttribute.objects.filter(organization=org).values_list("key", "value").distinct()
    ) | set(
        GroupAttribute.objects.filter(group__organization=org).values_list("key", "value").distinct()
    )
    seed_keys, seed_pairs = _expand_suggestions(IDENTITY_SUGGESTIONS)
    if include_system:
        sys_keys, sys_pairs = _expand_suggestions(SYSTEM_ATTRIBUTES)
        seed_keys |= sys_keys
        seed_pairs |= sys_pairs
    all_pairs = db_pairs | seed_pairs
    all_keys = {k for k, _ in all_pairs} | seed_keys
    return sorted(all_keys), sorted(all_pairs)


def get_resource_tag_suggestions(
    org: Organization, resource_type: str | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Returns (sorted_keys, sorted_key_value_pairs) for resource tag suggestions.

    When *resource_type* is given, only tags for that type are included.
    When ``None``, all resource tags across the org are returned (useful for
    the policy editor where the resource type may not be known yet).
    """
    qs = ResourceTag.objects.filter(organization=org)
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    db_pairs = set(qs.values_list("key", "value").distinct())

    if resource_type:
        seed = RESOURCE_SUGGESTIONS.get(resource_type, {})
    else:
        seed: dict[str, set[str]] = {}
        for type_suggestions in RESOURCE_SUGGESTIONS.values():
            for k, vals in type_suggestions.items():
                seed.setdefault(k, set()).update(vals)
    seed_keys, seed_pairs = _expand_suggestions(seed)

    all_pairs = db_pairs | seed_pairs
    all_keys = {k for k, _ in all_pairs} | seed_keys
    return sorted(all_keys), sorted(all_pairs)


