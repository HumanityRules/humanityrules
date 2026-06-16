"""
Organization invitation flow (control-plane only).

Lets an org admin invite someone by email to join the organization. The
invitee follows the link, signs in via WorkOS social login (creating a User
row through a slim onboarding variant if they're new), and is added as a
member of the inviter's org. No PDP / policy-proxy involvement — this is
purely a control-plane membership operation.

WorkOS-only for now: an OIDC/Okta org's invitee would land in /auth/login/
(WorkOS) and end up with a workos_user_id that the org's Okta-configured
policy proxy will reject. Both create_invite and accept_invite gate on
auth_provider == WORKOS. OIDC invites need a separate path through
/oidc/login/?org=<slug> + oidc_callback (TODO).
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..models import Organization, OrganizationInvite, OrganizationMembership, User
from ..services import abac_service
from . import base


INVITE_TTL_DAYS = 14


def _build_absolute_invite_url(request: HttpRequest, token) -> str:
    return request.build_absolute_uri(
        reverse("invite_accept", kwargs={"token": token}),
    )


@login_required
def create_invite(request: HttpRequest) -> HttpResponse:
    """Admin POSTs an email; we mint an invite row and surface the share URL."""
    if request.method != "POST":
        return HttpResponseNotAllowed(permitted_methods=["POST"])
    forbidden = base.require_org_admin(request=request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    if org.auth_provider != Organization.AuthProvider.WORKOS:
        messages.error(
            request=request,
            message="Invites are only available for WorkOS-authenticated organizations.",
        )
        return redirect(to="/settings/organization/")

    raw_email = request.POST.get("email", "").strip().lower()
    if not raw_email:
        messages.error(request=request, message="Email is required.")
        return redirect(to="/settings/organization/")

    # Reject inviting an existing member of this org. Membership is keyed on
    # User, but we only know the email here — match by case-insensitive email
    # against existing members.
    already_member = OrganizationMembership.objects.filter(
        organization=org, user__email__iexact=raw_email,
    ).exists()
    if already_member:
        messages.error(request=request, message=f"{raw_email} is already a member of this organization.")
        return redirect(to="/settings/organization/")

    invite = OrganizationInvite.objects.create(
        organization=org,
        email=raw_email,
        invited_by=request.user,
        role=OrganizationMembership.Role.MEMBER,
        expires_at=timezone.now() + timedelta(days=INVITE_TTL_DAYS),
    )
    share_url = _build_absolute_invite_url(request=request, token=invite.token)
    messages.success(
        request=request,
        message=f"Invite created for {raw_email}. Share this link: {share_url}",
    )
    return redirect(to="/settings/organization/")


@login_required
def revoke_invite(request: HttpRequest, invite_id) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(permitted_methods=["POST"])
    forbidden = base.require_org_admin(request=request)
    if forbidden:
        return forbidden

    invite = get_object_or_404(
        OrganizationInvite,
        pk=invite_id,
        organization=request.user.current_organization,
    )
    if invite.accepted_at is None and invite.revoked_at is None:
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
    return redirect(to="/settings/organization/")


def invite_status(invite: OrganizationInvite) -> str | None:
    """Return None if the invite is consumable; otherwise an error string."""
    if invite.revoked_at is not None:
        return "This invitation has been revoked."
    if invite.accepted_at is not None:
        return "This invitation has already been accepted."
    if invite.expires_at <= timezone.now():
        return "This invitation has expired."
    return None


def accept_invite(request: HttpRequest, token) -> HttpResponse:
    """
    Entry point for the invitee.

    Anonymous: render a "you're invited" landing page that points to login
    with `next=/invite/<token>/`. After login, control returns here.

    Authenticated: validate that the signed-in email matches the invite
    address, create the OrganizationMembership, mark the invite accepted,
    and switch the user's current organization to the joined one.
    """
    invite = get_object_or_404(OrganizationInvite, token=token)
    if invite.organization.auth_provider != Organization.AuthProvider.WORKOS:
        # Defense in depth: covers stale invites if a WorkOS org is ever
        # converted to OIDC. Should never trigger in practice — create_invite
        # rejects for non-WorkOS orgs at issue time.
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_error.html",
            context={
                "error": "This invitation is no longer valid for the organization's authentication setup.",
                "organization_name": invite.organization.name,
            },
            status=400,
        )
    error = invite_status(invite=invite)
    if error is not None:
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_error.html",
            context={"error": error, "organization_name": invite.organization.name},
            status=400,
        )

    if not request.user.is_authenticated:
        next_url = reverse("invite_accept", kwargs={"token": token})
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_landing.html",
            context={
                "invite": invite,
                "login_url": f"/auth/login/?next={next_url}",
            },
        )

    if request.user.email.lower() != invite.email.lower():
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_error.html",
            context={
                "error": (
                    f"You are signed in as {request.user.email}, but this invitation "
                    f"was sent to {invite.email}. Sign out and sign back in with the "
                    "invited address."
                ),
                "organization_name": invite.organization.name,
            },
            status=403,
        )

    # Without a workos_user_id, the org's WorkOS-configured policy proxy
    # would later fail to look this user up (PDP keys on workos_user_id for
    # provider=workos). Refuse the membership rather than create a half-
    # working one. Triggers when an OIDC-authed user clicks a WorkOS invite.
    if not request.user.workos_user_id:
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_error.html",
            context={
                "error": (
                    "This invitation must be accepted from a WorkOS-linked sign-in. "
                    "Sign out and sign back in with Google to accept."
                ),
                "organization_name": invite.organization.name,
            },
            status=403,
        )

    _accept_invite_for_user(invite=invite, user=request.user)
    return redirect(to="/dashboard/")


def _accept_invite_for_user(invite: OrganizationInvite, user: User) -> None:
    """Materialize the membership and switch current_organization."""
    with transaction.atomic():
        abac_service.materialize_membership(
            organization=invite.organization, user=user, role=invite.role,
        )
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])
        user.current_organization = invite.organization
        user.save(update_fields=["current_organization"])
