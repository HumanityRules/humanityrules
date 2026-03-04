import hashlib
import secrets
from functools import lru_cache
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.http import HttpResponseBadRequest
from workos import WorkOSClient

from ..models import Organization, OrganizationMembership, User


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
    if request.user.is_authenticated:
        return redirect("/dashboard/")

    org_slug = request.GET.get("org", "")
    try:
        org = Organization.objects.get(slug=org_slug, auth_provider=Organization.AuthProvider.OIDC)
    except Organization.DoesNotExist:
        return HttpResponseBadRequest("Organization not found")

    return _start_oidc_login(request, org)


def _start_workos_login(request):
    """Redirect to WorkOS AuthKit."""
    redirect_uri = f"{_build_base_uri(request)}/auth/callback/"
    authorization_url = _get_workos_client().user_management.get_authorization_url(
        provider="authkit",
        redirect_uri=redirect_uri,
    )
    return redirect(authorization_url)


def auth_login(request):
    """Redirects to WorkOS AuthKit for authentication."""
    if request.user.is_authenticated:
        return redirect("/dashboard/")
    return _start_workos_login(request)


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
            return redirect("/dashboard/")

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

    # Clean up session state
    request.session.pop("oidc_state", None)
    request.session.pop("oidc_org_slug", None)

    # Find or create user
    try:
        user = User.objects.get(oidc_sub=userinfo["sub"])
        user.email = userinfo["email"]
        user.first_name = userinfo["first_name"]
        user.last_name = userinfo["last_name"]
        user.save()
    except User.DoesNotExist:
        user = User.objects.create_user(
            username=userinfo["email"],
            email=userinfo["email"],
            first_name=userinfo["first_name"],
            last_name=userinfo["last_name"],
            oidc_sub=userinfo["sub"],
            current_organization=org,
        )
        OrganizationMembership.objects.create(
            user=user,
            organization=org,
            role=org.default_org_role,
        )

    login(request, user)
    return redirect("/dashboard/")


def auth_logout(request):
    """
    Logs the user out of Django session.
    """
    logout(request)
    return redirect("/")
