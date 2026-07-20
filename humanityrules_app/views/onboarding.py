from asgiref.sync import async_to_sync
from django.contrib.auth import login
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.utils.text import slugify

from .. import app_slugs
from ..models import App, AppTemplate, Environment, Organization, OrganizationInvite, User, Workspace
from ..services import abac_service
from ..services import template_deploy_service
from . import invites as invites_views


_INVITE_PATH_PREFIX = "/invite/"

_FIRST_AGENT_TEMPLATE_SLUG = "hermes-personal"

# Set right after org creation; gates the name-your-first-agent step so it is
# only reachable on the just-signed-up path, never for invitees or later visits.
_FIRST_AGENT_SESSION_FLAG = "onboarding_first_agent"


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

                abac_service.bootstrap_organization(organization=org, admin_user=user)

            # Clear session data and log in
            del request.session["pending_workos_user"]
            login(request, user)
            request.session[_FIRST_AGENT_SESSION_FLAG] = True

            return redirect("/onboarding/agent/")

    return render(request, "humanityrules_app/onboarding.html", {
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
            template_name="humanityrules_app/invites/invite_error.html",
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


def _first_agent_deploy_targets(org: Organization) -> tuple[AppTemplate, Workspace, Environment] | None:
    """Resolve template + workspace + environment for the first-agent step; None when any is missing."""
    template = AppTemplate.objects.filter(slug=_FIRST_AGENT_TEMPLATE_SLUG, is_active=True).first()
    workspace = Workspace.objects.filter(organization=org).order_by("created_at").first()
    environment = (
        Environment.objects
        .select_related("aws_account")
        .filter(aws_account__organization=org, status=Environment.Status.READY)
        .order_by("created_at")
        .first()
    )
    if template is None or workspace is None or environment is None:
        return None
    return template, workspace, environment


def _default_agent_name(user: User) -> str:
    """Build the dashless prefill for the agent-name input."""
    name_source = user.first_name.strip() or "My Agent"
    derived_name = app_slugs.derive_app_slug(value=name_source)
    return derived_name or "myagent"


def _validate_agent_name(org: Organization, agent_name: str) -> str | None:
    """Return an error message when the name is unusable, else None."""
    if not agent_name:
        return "Give your agent a name."
    app_slug = app_slugs.derive_app_slug(value=agent_name)
    if not app_slug:
        return "The name must contain at least one letter or number."
    if App.objects.filter(organization=org, slug=app_slug).exists():
        return f"An app with the name '{app_slug}' already exists in your organization."
    return None


def onboarding_agent(request: HttpRequest) -> HttpResponse:
    """
    Step 2 of onboarding: name the first agent, deploy it from the hermes
    template into the org's starter workspace + sandbox environment, and land
    on the app page where the deployment log streams. Skippable to /dashboard/.
    """
    if not request.user.is_authenticated:
        return redirect("/auth/login/")

    org = request.user.current_organization
    if org is None or not request.session.get(_FIRST_AGENT_SESSION_FLAG):
        return redirect("/dashboard/")

    targets = _first_agent_deploy_targets(org=org)
    if targets is None:
        request.session.pop(_FIRST_AGENT_SESSION_FLAG, None)
        return redirect("/dashboard/")
    template, workspace, environment = targets

    error = None
    agent_name = _default_agent_name(user=request.user)
    if request.method == "POST":
        agent_name = request.POST.get("agent_name", "").strip()
        error = _validate_agent_name(org=org, agent_name=agent_name)
        if error is None:
            try:
                deployment = async_to_sync(template_deploy_service.deploy_from_template)(
                    template=template,
                    organization=org,
                    workspace=workspace,
                    environment=environment,
                    app_name=agent_name,
                    app_slug=app_slugs.derive_app_slug(value=agent_name),
                    created_by=request.user,
                    runtime_variable_overrides={},
                    owner_username=request.user.username,
                    compute_mode=template.default_compute_mode,
                    label="",
                )
            except ValueError as exc:
                error = str(exc)
            else:
                request.session.pop(_FIRST_AGENT_SESSION_FLAG, None)
                # ?welcome=1 makes the app page show the first-run welcome dialog
                # over the live deployment log; the dialog strips it after display.
                return redirect(f"/apps/{deployment.app.slug}/?welcome=1")

    return render(request, "humanityrules_app/onboarding_agent.html", {
        "agent_name": agent_name,
        "hosted_zone": environment.shared_alb_hosted_zone,
        "is_sandbox": environment.aws_account.is_humr_sandbox,
        "error": error,
    })
