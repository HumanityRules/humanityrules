import hashlib
import logging
import secrets
from functools import lru_cache
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from workos import WorkOSClient

from ..models import Organization, OrganizationMembership, User
from ..services import abac

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_workos_client():
    """Lazily initialize WorkOS client on first use."""
    return WorkOSClient(
        api_key=settings.WORKOS_API_KEY,
        client_id=settings.WORKOS_CLIENT_ID,
    )


def _build_base_uri(request):
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}"


def _safe_next(request: HttpRequest, next_url: str) -> str | None:
    """Return *next_url* if it is a safe same-host redirect target, else None."""
    if not next_url:
        return None
    if url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


def _start_oidc_login(request, org):
    """Build Okta OIDC authorization URL and redirect."""
    state = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    request.session["oidc_state"] = state
    request.session["oidc_org_slug"] = org.slug

    params = urlencode({
        "client_id": org.oidc_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{_build_base_uri(request)}/oidc/callback/",
        "state": state,
    })
    authorize_url = f"{org.oidc_issuer_url}/v1/authorize?{params}"
    return redirect(authorize_url)


def _exchange_oidc_code(org, code, redirect_uri):
    """Exchange authorization code for tokens and fetch user info from OIDC provider."""
    token_url = f"{org.oidc_issuer_url}/v1/token"
    token_response = httpx.post(token_url, data={
        "client_id": org.oidc_client_id,
        "client_secret": org.oidc_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    userinfo_url = f"{org.oidc_issuer_url}/v1/userinfo"
    userinfo_response = httpx.get(userinfo_url, headers={
        "Authorization": f"Bearer {access_token}",
    })
    userinfo_response.raise_for_status()
    userinfo = userinfo_response.json()

    return {
        "sub": userinfo["sub"],
        "email": userinfo.get("email", ""),
        "first_name": userinfo.get("given_name", ""),
        "last_name": userinfo.get("family_name", ""),
    }


def oidc_login(request):
    """Start OIDC login for the given organization."""
    next_url = _safe_next(request=request, next_url=request.GET.get("next", ""))

    if request.user.is_authenticated:
        return redirect(next_url or "/dashboard/")

    org_slug = request.GET.get("org", "")
    try:
        org = Organization.objects.get(slug=org_slug, auth_provider=Organization.AuthProvider.OIDC)
    except Organization.DoesNotExist:
        return HttpResponseBadRequest("Organization not found")

    if next_url:
        request.session["oidc_next"] = next_url
    else:
        request.session.pop("oidc_next", None)

    return _start_oidc_login(request, org)


def _start_workos_login(request):
    """Redirect to WorkOS AuthKit."""
    redirect_uri = f"{_build_base_uri(request)}/auth/callback"
    authorization_url = _get_workos_client().user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=redirect_uri,
    )
    return redirect(authorization_url)


def _stash_post_login_redirect(request: HttpRequest) -> None:
    """Persist a validated `next` URL so auth_callback can pick it up after WorkOS."""
    next_url = _safe_next(request=request, next_url=request.GET.get("next", ""))
    if next_url:
        request.session["post_login_redirect"] = next_url
    else:
        request.session.pop("post_login_redirect", None)


def dev_login(request):
    """Auto-login as superuser for local development. Only available when DEBUG=True."""
    if not settings.DEBUG:
        return HttpResponseBadRequest("Not available")
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        return HttpResponseBadRequest("No superuser found")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    next_url = request.GET.get("next", "/dashboard/")
    return redirect(next_url)


def auth_login(request):
    """Redirects to WorkOS AuthKit for authentication."""
    if request.user.is_authenticated:
        next_url = _safe_next(request=request, next_url=request.GET.get("next", ""))
        return redirect(next_url or "/dashboard/")
    _stash_post_login_redirect(request=request)
    return _start_workos_login(request)


def _post_login_redirect_target(request: HttpRequest) -> str:
    """Pop the stashed redirect target. Default to /dashboard/."""
    return request.session.pop("post_login_redirect", "") or "/dashboard/"


def auth_callback(request):
    """Handles the OAuth callback from WorkOS."""
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    try:
        auth_response = _get_workos_client().user_management.authenticate_with_code(
            code=code,
        )

        workos_user = auth_response.user

        try:
            user = User.objects.get(workos_user_id=workos_user.id)
            user.email = workos_user.email
            user.first_name = workos_user.first_name or ""
            user.last_name = workos_user.last_name or ""
            user.save()
            login(request, user)
            return redirect(_post_login_redirect_target(request=request))

        except User.DoesNotExist:
            # New user - store WorkOS info in session for onboarding
            request.session["pending_workos_user"] = {
                "workos_user_id": workos_user.id,
                "email": workos_user.email,
                "first_name": workos_user.first_name or "",
                "last_name": workos_user.last_name or "",
            }
            return redirect("/onboarding/")

    except Exception as e:
        return HttpResponseBadRequest(f"Authentication failed: {str(e)}")


def _find_user_by_org_email(org: Organization, email: str) -> User | None:
    """Identity-link a WorkOS-onboarded user into OIDC by (org, email).

    Accepts two trust signals: the user already has an OrganizationMembership
    in the org, or they are the pending bootstrap admin. Email alone is never
    sufficient — org scoping is required to prevent an IdP asserting an
    arbitrary email from hijacking an unrelated account.
    """
    qs = User.objects.filter(email__iexact=email)
    member = qs.filter(organization_memberships__organization=org).first()
    if member is not None:
        return member
    if org.bootstrap_admin_email and email.lower() == org.bootstrap_admin_email.lower():
        return qs.first()
    return None


def oidc_callback(request):
    """Handles the OAuth callback from OIDC provider (Okta)."""
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    state = request.GET.get("state", "")
    if state != request.session.get("oidc_state", ""):
        return HttpResponseBadRequest("Invalid state parameter")

    org_slug = request.session.get("oidc_org_slug", "")
    try:
        org = Organization.objects.get(slug=org_slug)
    except Organization.DoesNotExist:
        return HttpResponseBadRequest("Organization not found")

    redirect_uri = f"{_build_base_uri(request)}/oidc/callback/"
    try:
        userinfo = _exchange_oidc_code(org, code, redirect_uri)
    except Exception as e:
        return HttpResponseBadRequest(f"OIDC authentication failed: {e}")

    logger.info("oidc login org=%s sub=%s email=%s", org.slug, userinfo["sub"], userinfo["email"])

    # Clean up session state (but preserve oidc_next until after login() is called,
    # since login() cycles the session).
    request.session.pop("oidc_state", None)
    request.session.pop("oidc_org_slug", None)
    next_url = _safe_next(request=request, next_url=request.session.pop("oidc_next", ""))
    post_login_redirect = next_url or "/dashboard/"

    # Canonical path: existing OIDC user, matched by stable sub.
    user = User.objects.filter(
        oidc_sub=userinfo["sub"],
        organization_memberships__organization=org,
    ).first()
    if user is not None:
        user.email = userinfo["email"]
        user.first_name = userinfo["first_name"]
        user.last_name = userinfo["last_name"]
        user.save()
        login(request, user)
        return redirect(post_login_redirect)
    if User.objects.filter(oidc_sub=userinfo["sub"]).exists():
        logger.error(
            "oidc sub belongs to a different organization login_org=%s sub=%s email=%s",
            org.slug, userinfo["sub"], userinfo["email"],
        )
        return HttpResponseBadRequest("OIDC sub belongs to a different organization")

    # Identity-linking path: an existing DOH user (typically WorkOS-onboarded)
    # hitting /oidc/login/ for the first time. Match on (org, email) and
    # back-fill oidc_sub so future logins take the canonical path.
    existing = _find_user_by_org_email(org=org, email=userinfo["email"])
    if existing is not None:
        if existing.oidc_sub and existing.oidc_sub != userinfo["sub"]:
            # Same email, different sub — upstream identity changed. Fail
            # loudly rather than silently re-point the row.
            logger.error(
                "oidc sub mismatch org=%s email=%s stored_sub=%s asserted_sub=%s",
                org.slug, userinfo["email"], existing.oidc_sub, userinfo["sub"],
            )
            return HttpResponseBadRequest("OIDC sub mismatch for existing user")
        existing.oidc_sub = userinfo["sub"]
        existing.email = userinfo["email"]
        existing.first_name = userinfo["first_name"]
        existing.last_name = userinfo["last_name"]
        existing.save()
        logger.info(
            "oidc backfilled sub org=%s email=%s sub=%s",
            org.slug, userinfo["email"], userinfo["sub"],
        )
        # If the matched row is the pending bootstrap admin (no membership in
        # this org yet), run the bootstrap path now.
        if (
            org.bootstrap_admin_email
            and userinfo["email"].lower() == org.bootstrap_admin_email.lower()
            and not OrganizationMembership.objects.filter(user=existing, organization=org).exists()
        ):
            abac.bootstrap_organization(organization=org, admin_user=existing)
            org.bootstrap_admin_email = ""
            org.save(update_fields=["bootstrap_admin_email"])
        login(request, existing)
        return redirect(post_login_redirect)

    # Brand-new user: create the row and either bootstrap the org (first admin)
    # or join as a default-role member.
    user = User.objects.create_user(
        username=userinfo["email"],
        email=userinfo["email"],
        first_name=userinfo["first_name"],
        last_name=userinfo["last_name"],
        oidc_sub=userinfo["sub"],
        current_organization=org,
    )
    if org.bootstrap_admin_email and userinfo["email"].lower() == org.bootstrap_admin_email.lower():
        abac.bootstrap_organization(organization=org, admin_user=user)
        org.bootstrap_admin_email = ""
        org.save(update_fields=["bootstrap_admin_email"])
    else:
        abac.materialize_membership(
            organization=org, user=user, role=org.default_org_role,
        )
    login(request, user)
    return redirect(post_login_redirect)


def auth_logout(request):
    """
    Logs the user out of Django session.
    """
    logout(request)
    return redirect("/")
