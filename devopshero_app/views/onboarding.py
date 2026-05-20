from django.contrib.auth import login
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.utils.text import slugify

from ..models import Organization, OrganizationInvite, User
from ..services import abac
from . import invites as invites_views


_INVITE_PATH_PREFIX = "/invite/"


def _pending_invite_for_session(request: HttpRequest) -> OrganizationInvite | None:
    """If post-login redirect is a still-consumable invite URL, return the invite.

    Revoked / expired / already-accepted invites return None so the user
    falls through to the regular onboarding flow (creating their own org)
    instead of being attached to the inviter's organization on a stale link.
    """
    target = request.session.get("post_login_redirect", "")
    if not target.startswith(_INVITE_PATH_PREFIX):
        return None
    token = target.removeprefix(_INVITE_PATH_PREFIX).strip("/")
    try:
        invite = OrganizationInvite.objects.select_related("organization").get(token=token)
    except (OrganizationInvite.DoesNotExist, ValueError):
        return None
    if invite.organization.auth_provider != Organization.AuthProvider.WORKOS:
        return None
    if invites_views.invite_status(invite=invite) is not None:
        return None
    return invite


def onboarding(request: HttpRequest) -> HttpResponse:
    """
    Handles new user onboarding - collects organization name and creates
    User + Organization + Membership in a single transaction.

    Variant: when the user landed here via an invite link, skip the "name
    your organization" step. The User row is created with `current_organization`
    set to the inviter's org, and the invite-accept view creates the membership.
    """
    # If already logged in, go to dashboard
    if request.user.is_authenticated:
        return redirect("/dashboard/")

    # Must have pending WorkOS user data from auth callback
    pending_user = request.session.get("pending_workos_user")
    if not pending_user:
        return redirect("/auth/login/")

    invite = _pending_invite_for_session(request=request)
    if invite is not None:
        return _onboarding_via_invite(request=request, pending_user=pending_user, invite=invite)

    if request.method == "POST":
        org_name = request.POST.get("organization_name", "").strip()

        if org_name:
            # Generate unique slug
            base_slug = slugify(org_name)
            slug = base_slug
            counter = 1
            while Organization.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            # Create everything in a single transaction
            with transaction.atomic():
                org = Organization.objects.create(name=org_name, slug=slug)

                user = User.objects.create(
                    workos_user_id=pending_user["workos_user_id"],
                    email=pending_user["email"],
                    username=pending_user["email"],
                    first_name=pending_user["first_name"],
                    last_name=pending_user["last_name"],
                    current_organization=org,
                )

                abac.bootstrap_organization(organization=org, admin_user=user)

            # Clear session data and log in
            del request.session["pending_workos_user"]
            login(request, user)

            return redirect("/dashboard/")

    return render(request, "devopshero_app/onboarding.html", {
        "email": pending_user["email"],
    })


def _onboarding_via_invite(
    request: HttpRequest,
    pending_user: dict,
    invite: OrganizationInvite,
) -> HttpResponse:
    """Slim onboarding for invitees: create User + membership atomically against the invite.

    Collapses User creation and invite acceptance into one transaction so the
    invitee never exists in the half-state of "current_organization set, but no
    OrganizationMembership" — and to avoid an extra round-trip through
    /invite/<token>/ that would just repeat work we already have in hand.
    """
    if pending_user["email"].lower() != invite.email.lower():
        return render(
            request=request,
            template_name="devopshero_app/invites/invite_error.html",
            context={
                "error": (
                    f"You signed in as {pending_user['email']}, but this invitation "
                    f"was sent to {invite.email}. Sign out and try again with the "
                    "invited address."
                ),
                "organization_name": invite.organization.name,
            },
            status=403,
        )

    with transaction.atomic():
        # If a User row with this email already exists (typically an
        # OIDC-onboarded user from another org clicking a WorkOS invite),
        # link the workos_user_id onto it instead of trying to create a
        # second row that would violate the username uniqueness constraint.
        # The invite token + email match are sufficient trust to attach the
        # workos identity to the existing row.
        user = User.objects.filter(email__iexact=pending_user["email"]).first()
        if user is not None:
            user.workos_user_id = pending_user["workos_user_id"]
            user.first_name = pending_user["first_name"]
            user.last_name = pending_user["last_name"]
            user.current_organization = invite.organization
            user.save(update_fields=[
                "workos_user_id", "first_name", "last_name", "current_organization",
            ])
        else:
            user = User.objects.create(
                workos_user_id=pending_user["workos_user_id"],
                email=pending_user["email"],
                username=pending_user["email"],
                first_name=pending_user["first_name"],
                last_name=pending_user["last_name"],
                current_organization=invite.organization,
            )
        invites_views._accept_invite_for_user(invite=invite, user=user)
    del request.session["pending_workos_user"]
    login(request, user)
    return redirect("/dashboard/")
