"""Public-access grants for agent webapps.

The panel on the app detail page lists live WebappPublicGrant rows; the
standalone confirm page at /apps/<slug>/public-access/new is the deep-link
target the agent's `webapps expose` command prints. Anyone who can see the
app can see the panel; creating and revoking grants is org-admin only —
opening a hostname to the anonymous internet is an org-boundary decision,
not a workspace one.
"""

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

import httpx
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import App, Deployment, DeploymentBlueprint, Environment, User, WebappPublicGrant
from ..services import abac_service
from . import abac_view_checks, base

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(WebappPublicGrant.SLUG_PATTERN_TEXT)

# Value of the form's expiry radio -> lifetime. 24h is the default: grants are
# demo-shaped, and auto-expiry beats remembering to revoke.
_EXPIRY_CHOICES: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "never": None,
}

# The confirm page's server-side reachability poll (the CP requests the public
# URL exactly like an anonymous visitor would). ~2s HTMX interval x max polls
# comfortably outlives the proxy's cached-deny TTL.
_LIVE_CHECK_TIMEOUT_SECONDS = 3.0
_LIVE_CHECK_MAX_POLLS = 15


def _get_app_for_user(request: HttpRequest, app_slug: str) -> App:
    """Get an app that belongs to the current user's organization."""
    return get_object_or_404(
        App.objects.select_related("workspace", "source_template", "organization"),
        slug=app_slug,
        organization=request.user.current_organization,
    )


def _org_admin_denied(request: HttpRequest) -> HttpResponse | None:
    if abac_service.is_org_admin(organization=request.user.current_organization, user=request.user):
        return None
    return HttpResponse("Only organization admins can manage public access.", status=403)


def _webapp_hosts_enabled(app: App) -> bool:
    return bool(app.source_template and app.source_template.enable_webapp_hosts)


def _app_hostname(app: App, environment: Environment) -> str | None:
    """The app's public hostname on *environment*, or None if not derivable."""
    zone = environment.shared_alb_hosted_zone
    if not zone:
        return None
    latest = Deployment.objects.filter(app=app, environment=environment).order_by("-created_at").first()
    subdomain = (latest.subdomain if latest else "") or app.slug
    return f"{subdomain}.{zone}"


def _public_url(grant: WebappPublicGrant) -> str | None:
    host = _app_hostname(app=grant.app, environment=grant.environment)
    if host is None:
        return None
    return f"https://{grant.slug}-{host}/"


def _deployed_environments(app: App) -> list[Environment]:
    """Environments this app has a blueprint in — the grantable targets."""
    environment_ids = (
        DeploymentBlueprint.objects.filter(app=app)
        .exclude(status=DeploymentBlueprint.Status.DISCARDED)
        .values_list("environment_id", flat=True)
        .distinct()
    )
    return list(Environment.objects.filter(id__in=environment_ids).select_related("aws_account").order_by("slug"))


def build_public_access_context(request: HttpRequest, app: App) -> dict[str, object]:
    """Context for the app-detail panel; empty grants list when webapp hosts are disabled."""
    grants = []
    if _webapp_hosts_enabled(app=app):
        grants = list(
            WebappPublicGrant.live().filter(app=app).select_related("environment", "granted_by").order_by("environment__slug", "slug"),
        )
    return {
        "public_access_enabled": _webapp_hosts_enabled(app=app),
        "public_grants": [{"grant": grant, "url": _public_url(grant=grant)} for grant in grants],
        "can_publish": abac_service.is_org_admin(organization=request.user.current_organization, user=request.user),
    }


@login_required
@require_GET
def webapp_public_access_new(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Confirm page: the one place a webapp becomes public. Deep-link target."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        query = request.META.get("QUERY_STRING", "")
        context["content_url"] = f"/apps/{app_slug}/public-access/new" + (f"?{query}" if query else "")
        return render(request, "humanityrules_app/app_shell.html", context=context)

    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = _org_admin_denied(request=request)
    if denied:
        return denied
    if not _webapp_hosts_enabled(app=app):
        return HttpResponse("This app's template does not serve webapps.", status=422)

    environments = _deployed_environments(app=app)
    prefill_slug = request.GET.get("slug", "")
    prefill_env = request.GET.get("env", "")
    selected_environment = next((e for e in environments if e.slug == prefill_env), None)
    if selected_environment is None and len(environments) == 1:
        selected_environment = environments[0]

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["app"] = app
    context["environments"] = [
        {"environment": e, "app_hostname": _app_hostname(app=app, environment=e), "selected": e == selected_environment}
        for e in environments
    ]
    context["prefill_slug"] = prefill_slug if _SLUG_RE.match(prefill_slug) else ""
    return render(request, "humanityrules_app/apps/app_public_access_new.html", context=context)


def _create_or_extend_grant(app: App, environment: Environment, slug: str, granted_by: User, expires_at: datetime | None) -> WebappPublicGrant:
    """One exposure window per row: a previous window that has lapsed gets its
    revoked_at stamped (satisfying the partial unique constraint) and a fresh
    row records the new window. A still-live grant is just extended.
    """
    with transaction.atomic():
        existing = (
            WebappPublicGrant.objects.select_for_update()
            .filter(app=app, environment=environment, slug=slug, revoked_at__isnull=True)
            .first()
        )
        if existing is not None and existing.is_live:
            existing.expires_at = expires_at
            existing.save(update_fields=["expires_at"])
            return existing
        if existing is not None:
            existing.revoked_at = timezone.now()
            existing.save(update_fields=["revoked_at"])
        return WebappPublicGrant.objects.create(
            app=app,
            environment=environment,
            slug=slug,
            granted_by=granted_by,
            expires_at=expires_at,
        )


@login_required
@require_POST
def webapp_public_access_create(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Create the grant and land on its status page (which self-checks the URL)."""
    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = _org_admin_denied(request=request)
    if denied:
        return denied
    if not _webapp_hosts_enabled(app=app):
        return HttpResponse("This app's template does not serve webapps.", status=422)

    slug = request.POST.get("slug", "").strip().lower()
    if not _SLUG_RE.match(slug):
        return HttpResponse("Invalid webapp name.", status=422)

    # Looked up by id, not slug: environment slugs are only unique per AWS
    # account, so an org with two accounts can hold same-slugged environments.
    try:
        environment_id = UUID(request.POST.get("environment", ""))
    except ValueError:
        return HttpResponse("Invalid environment.", status=422)
    environment = get_object_or_404(
        Environment.objects.filter(aws_account__organization=request.user.current_organization),
        id=environment_id,
    )
    blueprint_exists = (
        DeploymentBlueprint.objects.filter(app=app, environment=environment)
        .exclude(status=DeploymentBlueprint.Status.DISCARDED)
        .exists()
    )
    if not blueprint_exists:
        return HttpResponse("This app is not deployed to that environment.", status=422)

    expiry_key = request.POST.get("expiry", "")
    if expiry_key not in _EXPIRY_CHOICES:
        return HttpResponse("Invalid expiry.", status=422)
    lifetime = _EXPIRY_CHOICES[expiry_key]
    expires_at = timezone.now() + lifetime if lifetime is not None else None

    try:
        grant = _create_or_extend_grant(app=app, environment=environment, slug=slug, granted_by=request.user, expires_at=expires_at)
    except IntegrityError:
        # Lost a concurrent first-publish race on unique_unrevoked_webapp_grant:
        # select_for_update can't lock a row that doesn't exist yet. The winner's
        # row does exist now, so the retry locks it and takes the extend path.
        grant = _create_or_extend_grant(app=app, environment=environment, slug=slug, granted_by=request.user, expires_at=expires_at)

    logger.info(
        "webapp public grant created app=%s env=%s slug=%s by=%s expires=%s",
        app.slug, environment.slug, slug, request.user.username, expires_at,
    )
    return redirect(f"/apps/{app.slug}/public-access/{grant.id}/")


@login_required
@require_GET
def webapp_public_access_status(request: HttpRequest, app_slug: str, grant_id: UUID) -> HttpResponse:
    """Post-confirm landing: shows the public URL and polls until it answers."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = f"/apps/{app_slug}/public-access/{grant_id}/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request=request, resource=app.workspace, resource_type="workspace", action="workspace:view")
    if denied:
        return denied
    grant = get_object_or_404(WebappPublicGrant.objects.select_related("environment"), id=grant_id, app=app)

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["app"] = app
    context["grant"] = grant
    context["public_url"] = _public_url(grant=grant)
    return render(request, "humanityrules_app/apps/app_public_access_status.html", context=context)


def _webapp_answered_anonymously(response: httpx.Response) -> bool:
    """Whether *response* came from the webapp itself, not the layers in front of it.

    The not-yet-public failure modes hide behind sub-500 statuses: the policy
    proxy answers an anonymous navigation with a 302 off-host to the CP login
    (never 401/403 — those only reach authenticated callers), and Caddy answers
    an unrouted slug host with its literal 404 "unknown host" body (see the
    hermes_agent Caddyfile). Anything the webapp itself produced — its own 4xx,
    a same-host redirect — counts as reachable.
    """
    if response.is_redirect:
        target_host = urlparse(response.headers.get("location", "")).netloc
        return target_host in ("", response.request.url.host)
    if response.status_code in (401, 403):
        return False
    if response.status_code == 404 and response.text.strip() == "unknown host":
        return False
    return response.status_code < 500


@login_required
@require_GET
def webapp_public_access_check(request: HttpRequest, app_slug: str, grant_id: UUID) -> HttpResponse:
    """HTMX fragment: GET the public URL from the CP and report reachability.

    Server-side on purpose — a browser-side fetch would be blocked by CORS,
    while the CP hits the URL exactly like the anonymous visitor the grant is
    for, exercising PDP, proxy cache, and Caddy end to end. Self-terminating:
    the fragment re-polls only while pending and under the attempt budget.
    """
    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request=request, resource=app.workspace, resource_type="workspace", action="workspace:view")
    if denied:
        return denied
    grant = get_object_or_404(WebappPublicGrant.objects.select_related("environment"), id=grant_id, app=app)

    public_url = _public_url(grant=grant)
    poll_count = int(request.GET.get("n", "0") or "0")

    status = "pending"
    if public_url is None or not grant.is_live:
        status = "error"
    else:
        try:
            response = httpx.get(public_url, timeout=_LIVE_CHECK_TIMEOUT_SECONDS, follow_redirects=False)
            if _webapp_answered_anonymously(response=response):
                status = "live"
        except httpx.HTTPError:
            pass
    if status == "pending" and poll_count >= _LIVE_CHECK_MAX_POLLS:
        status = "gave-up"

    context = {
        "app": app,
        "grant": grant,
        "public_url": public_url,
        "status": status,
        "next_poll": poll_count + 1,
    }
    return render(request, "humanityrules_app/apps/_app_public_access_check.html", context=context)


@login_required
@require_GET
def webapp_public_access_revoke_confirm(request: HttpRequest, app_slug: str, grant_id: UUID) -> HttpResponse:
    """Return the standard confirmation modal for revoking public access."""
    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = _org_admin_denied(request=request)
    if denied:
        return denied

    grant = get_object_or_404(
        WebappPublicGrant.objects.select_related("environment"),
        id=grant_id,
        app=app,
        revoked_at__isnull=True,
    )
    return render(
        request=request,
        template_name="humanityrules_app/partials/_confirm_modal.html",
        context={
            "modal_title": "Revoke Public Access",
            "modal_message": (
                f"Close public access to {grant.slug} on {grant.environment.name}? "
                "Anonymous visitors will get a 403 within seconds."
            ),
            "confirm_url": f"/apps/{app.slug}/public-access/{grant.id}/revoke/",
            "confirm_label": "Revoke",
            "confirm_target": "#public-access-section",
            "confirm_swap": "outerHTML",
            "confirm_push_url": "false",
        },
    )


@login_required
@require_POST
def webapp_public_access_revoke(request: HttpRequest, app_slug: str, grant_id: UUID) -> HttpResponse:
    """Stamp revoked_at/revoked_by and return the refreshed panel."""
    app = _get_app_for_user(request=request, app_slug=app_slug)
    denied = _org_admin_denied(request=request)
    if denied:
        return denied

    grant = get_object_or_404(WebappPublicGrant, id=grant_id, app=app, revoked_at__isnull=True)
    grant.revoked_at = timezone.now()
    grant.revoked_by = request.user
    grant.save(update_fields=["revoked_at", "revoked_by"])
    logger.info(
        "webapp public grant revoked app=%s env=%s slug=%s by=%s",
        app.slug, grant.environment.slug, grant.slug, request.user.username,
    )

    context = {"app": app}
    context.update(build_public_access_context(request=request, app=app))
    return render(request, "humanityrules_app/apps/_app_public_access_section.html", context=context)
