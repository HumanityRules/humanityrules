"""
ABAC security views: People, Groups, Policies tabs.
"""

import json
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import (
    Group,
    GroupAttribute,
    GroupMembership,
    IdentityAttribute,
    Organization,
    OrganizationMembership,
    Policy,
    User,
)
from ..services import abac
from . import abac_view_checks
from . import base


# =============================================================================
# People
# =============================================================================


@login_required
def security_people(request: HttpRequest) -> HttpResponse:
    """List organization members with their effective attributes."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = "/security/people/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    memberships = OrganizationMembership.objects.filter(
        organization=org,
    ).select_related("user").order_by("user__email")

    # Gather effective attributes per member for display
    member_rows = []
    for membership in memberships:
        attrs = abac.get_effective_attributes(org, membership.user)
        member_rows.append({
            "user": membership.user,
            "membership": membership,
            "attributes": attrs,
        })

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "people"
    context["member_rows"] = member_rows
    return render(request, "devopshero_app/security/security_people.html", context=context)


@login_required
def security_people_detail(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Member detail with categorized attributes and group memberships."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = f"/security/people/{user_id}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    member = get_object_or_404(User, id=user_id)

    # Verify user belongs to org
    get_object_or_404(OrganizationMembership, organization=org, user=member)

    attrs = abac.get_effective_attributes(org, member)
    system_attrs = [(k, v, s) for k, v, s in attrs if s == "system"]
    direct_attrs_list = [(k, v, s) for k, v, s in attrs if s == "direct"]
    group_attrs = [(k, v, s) for k, v, s in attrs if s.startswith("group:")]

    direct_attr_objects = IdentityAttribute.objects.filter(organization=org, user=member).order_by("key", "value")
    group_memberships = GroupMembership.objects.filter(
        group__organization=org, user=member,
    ).select_related("group")
    available_groups = Group.objects.filter(organization=org).exclude(
        id__in=group_memberships.values_list("group_id", flat=True),
    ).order_by("name")

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "people"
    context["member"] = member
    context["system_attrs"] = system_attrs
    context["direct_attrs"] = direct_attrs_list
    context["direct_attr_objects"] = direct_attr_objects
    context["group_attrs"] = group_attrs
    context["group_memberships"] = group_memberships
    context["available_groups"] = available_groups
    context["suggested_keys"] = abac.get_identity_attribute_suggestion_keys(org)
    context["suggested_values"] = abac.get_identity_attribute_suggestion_values(org)
    return render(request, "devopshero_app/security/security_people_detail.html", context=context)


@login_required
@require_POST
def security_people_attribute_add(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Add a direct attribute to a member."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    member = get_object_or_404(User, id=user_id)
    get_object_or_404(OrganizationMembership, organization=org, user=member)

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        IdentityAttribute.objects.get_or_create(organization=org, user=member, key=key, value=value)

    return _render_people_attributes_partial(request, org, member)


@login_required
@require_POST
def security_people_attribute_remove(request: HttpRequest, user_id: UUID, attribute_id: UUID) -> HttpResponse:
    """Remove a direct attribute from a member."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    member = get_object_or_404(User, id=user_id)
    get_object_or_404(OrganizationMembership, organization=org, user=member)

    IdentityAttribute.objects.filter(id=attribute_id, organization=org, user=member).delete()

    return _render_people_attributes_partial(request, org, member)


@login_required
@require_POST
def security_people_group_add(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Add a member to a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    member = get_object_or_404(User, id=user_id)
    get_object_or_404(OrganizationMembership, organization=org, user=member)

    group_id = request.POST.get("group_id", "").strip()
    if group_id:
        group = get_object_or_404(Group, id=group_id, organization=org)
        GroupMembership.objects.get_or_create(group=group, user=member)

    return _render_people_attributes_partial(request, org, member)


@login_required
@require_POST
def security_people_group_remove(request: HttpRequest, user_id: UUID, membership_id: UUID) -> HttpResponse:
    """Remove a member from a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    member = get_object_or_404(User, id=user_id)
    get_object_or_404(OrganizationMembership, organization=org, user=member)

    GroupMembership.objects.filter(id=membership_id, user=member, group__organization=org).delete()

    return _render_people_attributes_partial(request, org, member)


def _render_people_attributes_partial(request: HttpRequest, org: Organization, member: User) -> HttpResponse:
    """Re-render the people attributes partial after mutation."""
    attrs = abac.get_effective_attributes(org, member)
    system_attrs = [(k, v, s) for k, v, s in attrs if s == "system"]
    direct_attrs_list = [(k, v, s) for k, v, s in attrs if s == "direct"]
    group_attrs = [(k, v, s) for k, v, s in attrs if s.startswith("group:")]
    direct_attr_objects = IdentityAttribute.objects.filter(organization=org, user=member).order_by("key", "value")
    group_memberships = GroupMembership.objects.filter(group__organization=org, user=member).select_related("group")
    available_groups = Group.objects.filter(organization=org).exclude(
        id__in=group_memberships.values_list("group_id", flat=True),
    ).order_by("name")

    return render(request, "devopshero_app/security/_people_attributes.html", {
        "member": member,
        "system_attrs": system_attrs,
        "direct_attrs": direct_attrs_list,
        "direct_attr_objects": direct_attr_objects,
        "group_attrs": group_attrs,
        "group_memberships": group_memberships,
        "available_groups": available_groups,
        "suggested_keys": abac.get_identity_attribute_suggestion_keys(org),
        "suggested_values": abac.get_identity_attribute_suggestion_values(org),
    })


# =============================================================================
# Groups
# =============================================================================


@login_required
def security_groups(request: HttpRequest) -> HttpResponse:
    """List all groups in the organization."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = "/security/groups/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    groups = Group.objects.filter(organization=org).order_by("name")

    # Annotate with counts
    group_rows = []
    for group in groups:
        group_rows.append({
            "group": group,
            "attribute_count": group.attributes.count(),
            "member_count": group.memberships.count(),
        })

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "groups"
    context["group_rows"] = group_rows
    return render(request, "devopshero_app/security/security_groups.html", context=context)


@login_required
@require_POST
def security_group_create(request: HttpRequest) -> HttpResponse:
    """Create a new group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if name:
        group, created = Group.objects.get_or_create(
            organization=org, name=name,
            defaults={"description": description},
        )
        return redirect("security_group_detail", group_id=group.id)

    return redirect("security_groups")


@login_required
def security_group_detail(request: HttpRequest, group_id: UUID) -> HttpResponse:
    """Group detail with attributes and members."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = f"/security/groups/{group_id}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)
    attributes = group.attributes.order_by("key", "value")
    members = group.memberships.select_related("user").order_by("user__email")

    # Users not in this group
    member_user_ids = members.values_list("user_id", flat=True)
    available_users = User.objects.filter(
        organization_memberships__organization=org,
    ).exclude(id__in=member_user_ids).order_by("email")

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "groups"
    context["group"] = group
    context["attributes"] = attributes
    context["members"] = members
    context["available_users"] = available_users
    context["suggested_keys"] = abac.get_identity_attribute_suggestion_keys(org)
    context["suggested_values"] = abac.get_identity_attribute_suggestion_values(org)
    return render(request, "devopshero_app/security/security_groups_detail.html", context=context)


@login_required
@require_POST
def security_group_delete(request: HttpRequest, group_id: UUID) -> HttpResponse:
    """Delete a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)
    group.delete()
    return redirect("security_groups")


@login_required
@require_POST
def security_group_attribute_add(request: HttpRequest, group_id: UUID) -> HttpResponse:
    """Add an attribute to a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        GroupAttribute.objects.get_or_create(group=group, key=key, value=value)

    return _render_group_attributes_partial(request, group)


@login_required
@require_POST
def security_group_attribute_remove(request: HttpRequest, group_id: UUID, attribute_id: UUID) -> HttpResponse:
    """Remove an attribute from a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)
    GroupAttribute.objects.filter(id=attribute_id, group=group).delete()

    return _render_group_attributes_partial(request, group)


@login_required
@require_POST
def security_group_member_add(request: HttpRequest, group_id: UUID) -> HttpResponse:
    """Add a member to a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)

    user_id = request.POST.get("user_id", "").strip()
    if user_id:
        user = get_object_or_404(User, id=user_id)
        get_object_or_404(OrganizationMembership, organization=org, user=user)
        GroupMembership.objects.get_or_create(group=group, user=user)

    return _render_group_members_partial(request, org, group)


@login_required
@require_POST
def security_group_member_remove(request: HttpRequest, group_id: UUID, membership_id: UUID) -> HttpResponse:
    """Remove a member from a group."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    group = get_object_or_404(Group, id=group_id, organization=org)
    GroupMembership.objects.filter(id=membership_id, group=group).delete()

    return _render_group_members_partial(request, org, group)


def _render_group_attributes_partial(request: HttpRequest, group: Group) -> HttpResponse:
    org = group.organization
    attributes = group.attributes.order_by("key", "value")
    return render(request, "devopshero_app/security/_group_attributes.html", {
        "group": group,
        "attributes": attributes,
        "suggested_keys": abac.get_identity_attribute_suggestion_keys(org),
        "suggested_values": abac.get_identity_attribute_suggestion_values(org),
    })


def _render_group_members_partial(request: HttpRequest, org: Organization, group: Group) -> HttpResponse:
    members = group.memberships.select_related("user").order_by("user__email")
    member_user_ids = members.values_list("user_id", flat=True)
    available_users = User.objects.filter(
        organization_memberships__organization=org,
    ).exclude(id__in=member_user_ids).order_by("email")
    return render(request, "devopshero_app/security/_group_members.html", {
        "group": group, "members": members, "available_users": available_users,
    })


# =============================================================================
# Policies
# =============================================================================


@login_required
def security_policies(request: HttpRequest) -> HttpResponse:
    """List all policies in the organization."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = "/security/policies/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    policies = Policy.objects.filter(organization=org).order_by("-is_system", "name")

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "policies"
    context["policies"] = policies
    return render(request, "devopshero_app/security/security_policies.html", context=context)


@login_required
def security_policy_create(request: HttpRequest) -> HttpResponse:
    """Create a new policy (GET = form, POST = save)."""
    if request.method == "GET" and not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = "/security/policies/create/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        resource_type = request.POST.get("resource_type", "").strip()
        identity_conditions = json.loads(request.POST.get("identity_conditions", "[]"))
        resource_conditions = json.loads(request.POST.get("resource_conditions", "[]"))
        actions = json.loads(request.POST.get("actions", "[]"))

        try:
            abac.validate_policy_conditions(
                identity_conditions=identity_conditions,
                resource_conditions=resource_conditions,
            )
        except ValidationError as e:
            existing_identity_keys = abac.get_identity_attribute_suggestion_keys(org)
            existing_identity_values = abac.get_identity_attribute_suggestion_values(org)
            existing_tag_keys = abac.get_resource_tag_suggestion_keys(org)
            existing_tag_values = abac.get_resource_tag_suggestion_values(org)
            context = base.get_app_shell_context(request=request, current_page="security")
            context["active_tab"] = "policies"
            context["policy"] = None
            context["existing_identity_keys"] = existing_identity_keys
            context["existing_identity_values"] = existing_identity_values
            context["existing_tag_keys"] = existing_tag_keys
            context["existing_tag_values"] = existing_tag_values
            context["error"] = e.message
            return render(request, "devopshero_app/security/security_policies_detail.html", context=context)

        if name and resource_type:
            Policy.objects.create(
                organization=org,
                name=name,
                resource_type=resource_type,
                identity_conditions=identity_conditions,
                resource_conditions=resource_conditions,
                actions=actions,
            )
        return redirect("security_policies")

    # GET — show form
    existing_identity_keys = abac.get_identity_attribute_suggestion_keys(org)
    existing_identity_values = abac.get_identity_attribute_suggestion_values(org)
    existing_tag_keys = abac.get_resource_tag_suggestion_keys(org)
    existing_tag_values = abac.get_resource_tag_suggestion_values(org)

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "policies"
    context["policy"] = None
    context["existing_identity_keys"] = existing_identity_keys
    context["existing_identity_values"] = existing_identity_values
    context["existing_tag_keys"] = existing_tag_keys
    context["existing_tag_values"] = existing_tag_values
    return render(request, "devopshero_app/security/security_policies_detail.html", context=context)


@login_required
def security_policy_detail(request: HttpRequest, policy_id: UUID) -> HttpResponse:
    """Edit an existing policy (GET = form, POST = update)."""
    if request.method == "GET" and not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = f"/security/policies/{policy_id}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    policy = get_object_or_404(Policy, id=policy_id, organization=org)

    if request.method == "POST":
        identity_conditions = json.loads(request.POST.get("identity_conditions", "[]"))
        resource_conditions = json.loads(request.POST.get("resource_conditions", "[]"))

        try:
            abac.validate_policy_conditions(
                identity_conditions=identity_conditions,
                resource_conditions=resource_conditions,
            )
        except ValidationError as e:
            existing_identity_keys = abac.get_identity_attribute_suggestion_keys(org)
            existing_identity_values = abac.get_identity_attribute_suggestion_values(org)
            existing_tag_keys = abac.get_resource_tag_suggestion_keys(org)
            existing_tag_values = abac.get_resource_tag_suggestion_values(org)
            context = base.get_app_shell_context(request=request, current_page="security")
            context["active_tab"] = "policies"
            context["policy"] = policy
            context["existing_identity_keys"] = existing_identity_keys
            context["existing_identity_values"] = existing_identity_values
            context["existing_tag_keys"] = existing_tag_keys
            context["existing_tag_values"] = existing_tag_values
            context["error"] = e.message
            return render(request, "devopshero_app/security/security_policies_detail.html", context=context)

        policy.name = request.POST.get("name", "").strip() or policy.name
        policy.resource_type = request.POST.get("resource_type", "").strip() or policy.resource_type
        policy.identity_conditions = identity_conditions
        policy.resource_conditions = resource_conditions
        policy.actions = json.loads(request.POST.get("actions", "[]"))
        policy.save()
        return redirect("security_policies")

    existing_identity_keys = abac.get_identity_attribute_suggestion_keys(org)
    existing_identity_values = abac.get_identity_attribute_suggestion_values(org)
    existing_tag_keys = abac.get_resource_tag_suggestion_keys(org)
    existing_tag_values = abac.get_resource_tag_suggestion_values(org)

    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "policies"
    context["policy"] = policy
    context["existing_identity_keys"] = existing_identity_keys
    context["existing_identity_values"] = existing_identity_values
    context["existing_tag_keys"] = existing_tag_keys
    context["existing_tag_values"] = existing_tag_values
    return render(request, "devopshero_app/security/security_policies_detail.html", context=context)


@login_required
@require_POST
def security_policy_delete(request: HttpRequest, policy_id: UUID) -> HttpResponse:
    """Delete a policy."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    org = request.user.current_organization
    policy = get_object_or_404(Policy, id=policy_id, organization=org)
    policy.delete()
    return redirect("security_policies")


