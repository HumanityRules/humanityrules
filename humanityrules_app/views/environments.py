import json
import logging
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

import humanityrules_app.models as models
from humanityrules_app.services import abac_service
from humanityrules_app.services import infra_customer
from humanityrules_app.services.jobs import environment_job_service

from . import abac_view_checks
from . import base

logger = logging.getLogger(__name__)

# Sentinel option for "no custom domain" in the setup-form domain dropdown.
NO_DOMAIN_LABEL = "None (HTTP only)"

# Cap the provisioning-log fragment so a runaway log can't blow up the page render.
MAX_ENVIRONMENT_LOG_LINES = 1000

# Curated region list for the non-agent setup form. Not exhaustive; covers the common
# deployment regions. The provisioning pipeline accepts any valid region string.
COMMON_AWS_REGIONS = (
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "eu-west-1",
    "eu-west-2",
    "eu-central-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
)


def _get_environment_for_user(request: HttpRequest, environment_id: UUID) -> models.Environment:
    """Load an environment that belongs to the current organization."""
    return get_object_or_404(
        models.Environment.objects.select_related("aws_account").exclude(status=models.Environment.Status.DISCARDED),
        id=environment_id,
        aws_account__organization=request.user.current_organization,
    )


def _get_environment_or_none(request: HttpRequest, environment_id: UUID) -> models.Environment | None:
    """Load an org-scoped environment, or None if it no longer exists (e.g. deleted by teardown)."""
    return (
        models.Environment.objects.select_related("aws_account")
        .exclude(status=models.Environment.Status.DISCARDED)
        .filter(id=environment_id, aws_account__organization=request.user.current_organization)
        .first()
    )


def _hx_redirect_to_environments() -> HttpResponse:
    """HTMX client-side redirect to the environments list, for polls whose environment is gone."""
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("environments")
    return response


def _is_environment_teardown_blocked(environment: models.Environment) -> bool:
    """Shared sandbox infra must not be torn down from the UI."""
    return environment.aws_account.is_humr_sandbox


def populate_environment_entrypoint(environment: models.Environment) -> models.Environment:
    """Attach the primary navigation target for an environment card.

    Every card routes to the detail page — it hosts the provisioning-log tab and the retry
    action, and covers an environment at any stage. The agent setup editor (when enabled) is
    reached only from the New Environment entry point, the single gate for the agent flow.
    """
    environment.primary_url = reverse("environment_detail", kwargs={"environment_id": environment.id})
    return environment


def build_environments_context(request: HttpRequest) -> dict[str, object]:
    """Build the shared context for the environments index page."""
    context = base.get_app_shell_context(request=request, current_page="environments")

    environment_list = (
        models.Environment.objects
        .filter(aws_account__organization=request.user.current_organization)
        .exclude(status=models.Environment.Status.DISCARDED)
        .select_related("aws_account")
        .order_by("aws_account__name", "name")
    )
    environment_list = abac_service.filter_permitted_resources(
        request.user.current_organization,
        request.user,
        environment_list,
        "environment",
        "environment:view",
    )
    environment_list = [
        populate_environment_entrypoint(environment=environment)
        for environment in environment_list
    ]

    # Exclude the shared sandbox account: its one environment is auto-managed by
    # sandbox_service.ensure_org_sandbox, and users must not create environments in it
    # (sandbox infra is shared and teardown is blocked, so a failed one would get stuck).
    aws_accounts = models.AWSAccount.objects.filter(
        organization=request.user.current_organization,
        status=models.AWSAccount.Status.CONNECTED,
        is_humr_sandbox=False,
    ).order_by("name")

    context["environments"] = environment_list
    context["aws_accounts"] = aws_accounts
    return context


def build_environment_detail_context(request: HttpRequest, environment: models.Environment) -> dict[str, object]:
    """Build the shared context for environment detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="environments")
    env_apps = models.App.objects.filter(
        environment=environment,
    ).select_related("workspace").order_by("name")
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    org = request.user.current_organization
    can_admin = abac_service.check_action(org, request.user, environment, "environment", "environment:admin")

    # Provisioning-log tab: shown once there's something to show (logs exist or a
    # provisioning/teardown is in flight), and opened by default while in flight.
    has_logs = models.EnvironmentLog.objects.filter(environment=environment).exists()
    context["environment"] = environment
    context["env_apps"] = env_apps
    context["tags"] = tags
    context["tags_json"] = json.dumps([{"key": tag.key, "value": tag.value} for tag in tags])
    context["can_admin"] = can_admin
    context["log_tab_enabled"] = has_logs or environment.is_transient
    context["initial_tab"] = "logs" if environment.is_transient else "content"
    context["url_base"] = f"/environments/{environment.id}/tags/"
    context["suggested_keys"], context["suggested_values"] = abac_service.get_resource_tag_suggestions(org, "environment")
    return context


@login_required
def environments(request: HttpRequest) -> HttpResponse:
    """List all environments in the current organization's AWS accounts."""
    context = build_environments_context(request=request)

    if request.htmx:
        return render(request, "humanityrules_app/environments/environments.html", context=context)

    context["content_url"] = "/environments/"
    return render(request, "humanityrules_app/app_shell.html", context=context)


@login_required
def environment_detail(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Show environment detail with deployments."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    context = build_environment_detail_context(request=request, environment=environment)

    if request.htmx:
        return render(request, "humanityrules_app/environments/environment_detail.html", context=context)

    context["content_url"] = f"/environments/{environment.id}/"
    return render(request, "humanityrules_app/app_shell.html", context=context)


def _load_connected_aws_account(request: HttpRequest, aws_account_id: str) -> models.AWSAccount:
    """Load a CONNECTED, non-sandbox AWS account in the current org (404 on missing/invalid/sandbox).

    The sandbox account is excluded so a crafted POST can't target it for environment creation,
    even though it never appears in the account pickers.
    """
    return get_object_or_404(
        models.AWSAccount,
        id=aws_account_id,
        organization=request.user.current_organization,
        status=models.AWSAccount.Status.CONNECTED,
        is_humr_sandbox=False,
    )


def _list_hosted_zone_options(aws_account: models.AWSAccount) -> tuple[list[dict[str, str]], bool]:
    """Return (domain options, lookup_failed) for the setup form's domain dropdown.

    Options always lead with the "no domain" sentinel, followed by the account's public
    Route53 hosted zones. An environment owns its hosted zone exclusively, so zones already
    claimed by another environment in the account are left out. On any AWS failure we
    return (sentinel-only, True) so the form can fall back to a free-text domain input
    instead of an empty dropdown.
    """
    options: list[dict[str, str]] = [{"id": "", "name": NO_DOMAIN_LABEL}]
    try:
        session = infra_customer.iam_utils.get_assumed_role_session(
            access_key=settings.HUMR_AWS_ACCESS_KEY,
            secret_key=settings.HUMR_AWS_SECRET_KEY,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region="us-east-1",  # Route53 is global; pinned for the session.
        )
        zones = infra_customer.route53_utils.list_hosted_zones(session=session)
    except Exception:
        logger.exception("Could not list Route53 hosted zones for AWS account %s", aws_account.id)
        return options, True

    claimed_zones = set(models.Environment.zone_claimants(aws_account=aws_account).values_list("shared_alb_hosted_zone", flat=True))
    for zone in zones:
        name = zone["name"].rstrip(".")
        if name and name not in claimed_zones:
            options.append({"id": name, "name": name})
    return options, False


def _connected_aws_accounts(request: HttpRequest) -> list[models.AWSAccount]:
    """Connected, non-sandbox AWS accounts in the current org, ordered by name."""
    return list(
        models.AWSAccount.objects.filter(
            organization=request.user.current_organization,
            status=models.AWSAccount.Status.CONNECTED,
            is_humr_sandbox=False,
        ).order_by("name")
    )


def _aws_account_label(aws_account: models.AWSAccount) -> str:
    """Display label for an AWS account in the picker dropdown."""
    if aws_account.aws_account_id:
        return f"{aws_account.name} ({aws_account.aws_account_id})"
    return aws_account.name


def _render_environment_setup_form(request: HttpRequest, aws_account: models.AWSAccount, form_values: dict[str, str], errors: dict[str, str]) -> HttpResponse:
    """Render the non-agent environment setup form fragment."""
    selected_region = form_values.get("aws_region") or "us-east-1"
    selected_domain = form_values.get("shared_alb_hosted_zone", "")
    domain_options, domain_lookup_failed = _list_hosted_zone_options(aws_account=aws_account)
    accounts = _connected_aws_accounts(request=request)

    context = base.get_app_shell_context(request=request, current_page="environments")
    context.update({
        "aws_account": aws_account,
        "account_options": [{"id": str(account.id), "name": _aws_account_label(account)} for account in accounts],
        "selected_account_id": str(aws_account.id),
        "selected_account_label": _aws_account_label(aws_account),
        "region_options": [{"id": region, "name": region} for region in COMMON_AWS_REGIONS],
        "selected_region": selected_region,
        "domain_options": domain_options,
        "domain_lookup_failed": domain_lookup_failed,
        "selected_domain": selected_domain,
        "selected_domain_label": selected_domain or NO_DOMAIN_LABEL,
        "form_values": form_values,
        "errors": errors,
    })
    return render(request, "humanityrules_app/environments/environment_setup_form.html", context=context)


def _handle_environment_setup_submit(request: HttpRequest) -> HttpResponse:
    """Validate the setup form and create a PENDING environment (the job worker provisions it)."""
    aws_account = _load_connected_aws_account(request=request, aws_account_id=request.POST.get("aws_account", "").strip())

    name = request.POST.get("name", "").strip()
    region = request.POST.get("aws_region", "").strip()
    hosted_zone = request.POST.get("shared_alb_hosted_zone", "").strip()
    form_values = {"name": name, "aws_region": region, "shared_alb_hosted_zone": hosted_zone}

    errors: dict[str, str] = {}
    slug = slugify(name)
    if not name:
        errors["name"] = "Name is required."
    elif not slug:
        errors["name"] = "Name must contain letters or numbers."
    elif models.Environment.objects.filter(aws_account=aws_account, slug=slug).exists():
        errors["name"] = f"An environment with the name \"{name}\" already exists in this account."
    if not region:
        errors["aws_region"] = "Region is required."
    if hosted_zone:
        claimant = models.Environment.zone_claimants(aws_account=aws_account).filter(shared_alb_hosted_zone=hosted_zone).first()
        if claimant:
            errors["shared_alb_hosted_zone"] = (
                f"\"{hosted_zone}\" already belongs to the environment \"{claimant.name}\". "
                "An environment owns its hosted zone exclusively — choose a different zone."
            )

    if errors:
        return _render_environment_setup_form(request=request, aws_account=aws_account, form_values=form_values, errors=errors)

    environment = models.Environment.objects.create(
        aws_account=aws_account,
        name=name,
        slug=slug,
        aws_region=region,
        shared_alb_hosted_zone=hosted_zone,
        status=models.Environment.Status.PENDING,
        status_message="Created via environment setup form",
    )

    # Hand off to the detail page; it auto-opens the provisioning log while the env is transient.
    context = build_environment_detail_context(request=request, environment=environment)
    response = render(request, "humanityrules_app/environments/environment_detail.html", context=context)
    response["HX-Push-Url"] = reverse("environment_detail", kwargs={"environment_id": environment.id})
    return response


@login_required
def environment_setup_form(request: HttpRequest) -> HttpResponse:
    """Form-based (non-agent) environment provisioning. GET renders the form; POST creates the environment."""
    if not request.htmx and request.method == "GET":
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="humanityrules_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    if request.method == "POST":
        return _handle_environment_setup_submit(request=request)

    accounts = _connected_aws_accounts(request=request)
    if not accounts:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["account_options"] = []
        return render(request, "humanityrules_app/environments/environment_setup_form.html", context=context)

    # Account comes from the in-page dropdown (it re-fetches this view on change to refresh the
    # account's hosted zones); default to the first account on the initial open. Name and region
    # are echoed back so switching accounts doesn't wipe what the user already typed. Domain is
    # intentionally not preserved across an account switch — hosted zones are account-specific.
    account_id = request.GET.get("aws_account", "").strip()
    aws_account = _load_connected_aws_account(request=request, aws_account_id=account_id) if account_id else accounts[0]
    form_values = {
        "name": request.GET.get("name", "").strip(),
        "aws_region": request.GET.get("aws_region", "").strip(),
    }
    return _render_environment_setup_form(request=request, aws_account=aws_account, form_values=form_values, errors={})


@login_required
@require_GET
def environment_provisioning_log(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Render the environment's provisioning log fragment; self-polls every 1s while transient.

    Redirects to the environments list if the environment is gone (teardown deletes it at the end),
    so the poll lands the user on a live page instead of a dead detail page.
    """
    environment = _get_environment_or_none(request=request, environment_id=environment_id)
    if environment is None:
        return _hx_redirect_to_environments()

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    # Fetch newest-first so the cap keeps the tail, then reverse to chronological for display.
    recent = list(
        models.EnvironmentLog.objects.filter(environment=environment).order_by("-created_at")[: MAX_ENVIRONMENT_LOG_LINES + 1]
    )
    truncated = len(recent) > MAX_ENVIRONMENT_LOG_LINES
    logs = list(reversed(recent[:MAX_ENVIRONMENT_LOG_LINES]))

    context = {
        "environment": environment,
        "logs": logs,
        "truncated": truncated,
        "max_lines": MAX_ENVIRONMENT_LOG_LINES,
    }
    return render(request, "humanityrules_app/environments/_environment_provisioning_log.html", context=context)


@login_required
@require_GET
def environment_status(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Render the environment status pill; self-polls every 5s while transient and OOB-swaps the header actions.

    Redirects to the environments list if the environment is gone (e.g. teardown finished and deleted it).
    """
    environment = _get_environment_or_none(request=request, environment_id=environment_id)
    if environment is None:
        return _hx_redirect_to_environments()

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    context = base.get_app_shell_context(request=request, current_page="environments")
    context["environment"] = environment
    context["with_oob"] = True
    return render(request, "humanityrules_app/environments/_environment_status.html", context=context)


@login_required
@require_POST
def environment_retry(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Re-queue a failed environment for provisioning by flipping its status back to PENDING."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    transitioned = environment_job_service.transition_status(
        environment_id=environment.id,
        expected_statuses=(models.Environment.Status.ERROR,),
        new_status=models.Environment.Status.PENDING,
        status_message="Retry triggered via web UI",
    )
    if not transitioned:
        return HttpResponse(status=422)

    environment.refresh_from_db(fields=["status", "status_message", "updated_at"])

    context = build_environment_detail_context(request=request, environment=environment)
    return render(request, "humanityrules_app/environments/environment_detail.html", context=context)


@login_required
def environment_teardown_confirm(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Return the environment teardown confirmation modal HTML."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    if _is_environment_teardown_blocked(environment=environment):
        return HttpResponse(status=403)

    return render(request, "humanityrules_app/partials/_confirm_modal.html", {
        "modal_title": "Tear Down Environment",
        "modal_message": f'Are you sure you want to tear down "{environment.name}"? This will destroy all deployments in the environment and delete the underlying infrastructure (VPC, ECS cluster). This action cannot be undone.',
        "confirm_url": f"/environments/{environment.id}/teardown/",
        "confirm_label": "Tear Down",
    })


@login_required
@require_POST
def environment_teardown(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Queue teardown for an environment."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    if _is_environment_teardown_blocked(environment=environment):
        return HttpResponse(status=403)

    try:
        environment_job_service.queue_teardown(
            environment_id=environment.id,
            status_message="Teardown triggered via web UI",
        )
    except environment_job_service.EnvironmentJobAdmissionError:
        return HttpResponse(status=422)

    environment.refresh_from_db(fields=["status", "status_message", "updated_at"])

    context = build_environment_detail_context(request=request, environment=environment)
    return render(request, "humanityrules_app/environments/environment_detail.html", context=context)


@login_required
@require_POST
def environment_tag_add(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Add a tag to an environment. Returns updated tag partial."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        models.ResourceTag.objects.get_or_create(
            organization=request.user.current_organization,
            resource_type="environment",
            environment=environment,
            key=key,
            value=value,
        )

    org = request.user.current_organization
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    url_base = f"/environments/{environment.id}/tags/"
    return render(request, "humanityrules_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#environment-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "environment"))),
    })


@login_required
@require_POST
def environment_tag_remove(request: HttpRequest, environment_id: UUID, tag_id: UUID) -> HttpResponse:
    """Remove a tag from an environment. Returns updated tag partial."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    models.ResourceTag.objects.filter(id=tag_id, environment=environment).delete()

    org = request.user.current_organization
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    url_base = f"/environments/{environment.id}/tags/"
    return render(request, "humanityrules_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#environment-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "environment"))),
    })


@login_required
@require_POST
def environment_tags_save(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Bulk-save environment tags. Replaces all existing tags with the submitted array."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    org = request.user.current_organization
    tags_data = json.loads(request.POST.get("tags", "[]"))

    models.ResourceTag.objects.filter(environment=environment).delete()
    seen = set()
    for tag in tags_data:
        key = tag.get("key", "").strip()
        value = tag.get("value", "").strip()
        if key and value and (key, value) not in seen:
            seen.add((key, value))
            models.ResourceTag.objects.create(
                organization=org, resource_type="environment", environment=environment,
                key=key, value=value,
            )

    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    suggested_keys, suggested_values = abac_service.get_resource_tag_suggestions(org, "environment")
    return render(request, "humanityrules_app/partials/_security_tags_section.html", {
        "can_admin": True,
        "tags_title": "Environment Tags",
        "tags_json": json.dumps([{"key": t.key, "value": t.value} for t in tags]),
        "url_base": f"/environments/{environment.id}/tags/",
        "suggested_keys": suggested_keys,
        "suggested_values": suggested_values,
    })
