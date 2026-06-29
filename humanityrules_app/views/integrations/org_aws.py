import json
import logging
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ...models import AWSAccount, Organization
from .org_shared_keys import SHARED_KEYS_URL
from .. import base

logger = logging.getLogger(__name__)


@login_required
def integrations_root(request: HttpRequest) -> HttpResponse:
    """Redirect /integrations/ to the default tab (Provider Keys)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden
    return HttpResponseRedirect(SHARED_KEYS_URL)


@login_required
def integrations_org_aws_accounts(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = "/integrations/org/aws-accounts/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    return _render_accounts_list(request=request, org=request.user.current_organization)


def _render_accounts_list(request: HttpRequest, org: Organization) -> HttpResponse:
    """Render the AWS accounts list fragment, annotated with each account's environment count."""
    accounts = AWSAccount.objects.filter(organization=org).annotate(environment_count=Count("environments"))
    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "aws-accounts"
    context["aws_accounts"] = accounts
    # Drives the list's self-terminating 30s poll: keep refreshing while any account awaits its callback.
    context["has_pending_accounts"] = accounts.filter(status=AWSAccount.Status.PENDING).exists()
    return render(request, "humanityrules_app/integrations/aws_accounts.html", context=context)


@login_required
def integrations_org_aws_accounts_add(request: HttpRequest) -> HttpResponse:
    """Render the Add AWS Account modal dialog and handle account creation."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if request.method == "POST":
        name = request.POST.get("name", "").strip()

        if not name:
            response = HttpResponse("")
            response["HX-Trigger"] = '{"validationError": "Please enter an AWS account name"}'
            return response

        org = request.user.current_organization

        existing = AWSAccount.objects.filter(organization=org, name=name).first()
        if existing:
            if existing.status in (AWSAccount.Status.PENDING, AWSAccount.Status.ERROR):
                return _connect_waiting_response(request=request, account=existing)
            else:
                response = HttpResponse("")
                response["HX-Trigger"] = '{"validationError": "An AWS account with this name is already connected"}'
                return response

        aws_account = AWSAccount.objects.create(
            organization=org,
            name=name,
            created_by=request.user,
        )

        return _connect_waiting_response(request=request, account=aws_account)

    return render(request, "humanityrules_app/integrations/aws_account_add_modal.html")


def _install_callback_base_url(request: HttpRequest) -> str:
    """The origin the admin reached us on — where the install Lambda should report back.

    Derived from the request so the connect flow works on whatever host serves it (prod,
    or a dev box behind an ngrok tunnel) with no per-environment configuration.
    """
    return request.build_absolute_uri("/").rstrip("/")


def _render_connect_poll(request: HttpRequest, account: AWSAccount) -> HttpResponse:
    """Render the invisible self-terminating poll that watches a pending account for connection."""
    return render(request, "humanityrules_app/integrations/_aws_connect_poll.html", {"account": account})


def _connect_waiting_response(request: HttpRequest, account: AWSAccount) -> HttpResponse:
    """POST response that opens the CloudFormation page (once) and arms the connect poll."""
    response = _render_connect_poll(request=request, account=account)
    url = account.get_cloudformation_url(api_endpoint=_install_callback_base_url(request=request))
    response["HX-Trigger"] = f'{{"openCloudFormation": "{url}"}}'
    return response


@login_required
def integrations_org_aws_accounts_status(request: HttpRequest, account_id: str) -> HttpResponse:
    """Poll endpoint for the connect modal: re-arm while pending, close + refresh once connected."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    account = AWSAccount.objects.filter(organization=org, id=account_id).first()

    # Connected (or the row is gone): close the modal and refresh the list behind it. Emptying
    # #modal-container removes the poll element, which stops its `every 5s` interval.
    if account is None or account.status == AWSAccount.Status.CONNECTED:
        response = HttpResponse("")
        response["HX-Retarget"] = "#modal-container"
        response["HX-Reswap"] = "innerHTML"
        response["HX-Trigger"] = "awsAccountsChanged"
        return response

    # Still pending — nothing to swap (hx-swap="none"); the interval keeps polling.
    return HttpResponse(status=204)


@login_required
def integrations_org_aws_accounts_edit(request: HttpRequest, account_id: str) -> HttpResponse:
    """Render the Edit AWS Account modal (GET) and update the account (POST)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    account = AWSAccount.objects.filter(organization=org, id=account_id).first()
    if account is None:
        raise Http404("AWS account not found.")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return _render_edit_modal(request=request, account=account, name=name, error="Please enter an AWS account name.")
        if AWSAccount.objects.filter(organization=org, name=name).exclude(id=account.id).exists():
            return _render_edit_modal(request=request, account=account, name=name, error="An AWS account with this name already exists.")

        account.name = name
        account.save(update_fields=["name", "updated_at"])

        response = HttpResponse("")
        response["HX-Trigger"] = "awsAccountsChanged"
        return response

    return _render_edit_modal(request=request, account=account, name=account.name, error="")


def _render_edit_modal(request: HttpRequest, account: AWSAccount, name: str, error: str) -> HttpResponse:
    """Render the edit modal, echoing the posted name back on validation error."""
    context = {
        "account": account,
        "name": name,
        "error": error,
        "cloudformation_url": account.get_cloudformation_url(api_endpoint=_install_callback_base_url(request=request)),
    }
    return render(request, "humanityrules_app/integrations/aws_account_edit_modal.html", context=context)


def _is_disconnect_blocked(account: AWSAccount) -> bool:
    """Disconnect is blocked while any environment still pins this account.

    Disconnect only removes our record, but the Environment FK is CASCADE — deleting the
    account would silently drop those environments' records and orphan their AWS infra, so
    they must be torn down first.
    """
    return account.environments.exists()


@login_required
def integrations_org_aws_accounts_disconnect_confirm(request: HttpRequest, account_id: str) -> HttpResponse:
    """Return the disconnect confirmation modal.

    Disconnect is record-only: it never touches the customer account. The CloudFormation
    install stack is left in place — the customer deletes it themselves to fully revoke access.
    """
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    account = AWSAccount.objects.filter(organization=org, id=account_id).first()
    if account is None:
        raise Http404("AWS account not found.")
    if _is_disconnect_blocked(account=account):
        return HttpResponse(status=403)

    if account.aws_account_id:
        message = (
            f'Remove "{account.name}" from HumanityRules? This removes our record only — it does not '
            f'touch your AWS account. The CloudFormation stack {account.get_install_stack_name()} stays in '
            f'account {account.aws_account_id}; delete it from the AWS console ({account.INSTALL_STACK_REGION}) '
            f'to fully revoke our access. This cannot be undone.'
        )
    else:
        message = f'Remove "{account.name}" from HumanityRules? It was never connected, so there is nothing to clean up in AWS.'

    return render(request, "humanityrules_app/partials/_confirm_modal.html", {
        "modal_title": "Disconnect AWS Account",
        "modal_message": message,
        "confirm_url": f"/integrations/org/aws-accounts/{account.id}/disconnect/",
        "confirm_label": "Remove",
    })


@login_required
@require_POST
def integrations_org_aws_accounts_disconnect(request: HttpRequest, account_id: str) -> HttpResponse:
    """Remove the AWS account record. Record-only — the customer's install stack is left in place."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    account = AWSAccount.objects.filter(organization=org, id=account_id).first()
    if account is None:
        raise Http404("AWS account not found.")
    if _is_disconnect_blocked(account=account):
        return HttpResponse(status=403)

    logger.info("Removing AWS account record '%s' (org=%s) — install stack left in place", account.name, org.slug)
    account.delete()

    # Posted from the confirm modal (hx-push-url): land the address bar on the AWS accounts list,
    # not this POST-only disconnect URL (which 405s on reload).
    response = _render_accounts_list(request=request, org=org)
    response["HX-Push-Url"] = "/integrations/org/aws-accounts/"
    return response


@csrf_exempt
@require_POST
def aws_install_account_callback(request: HttpRequest) -> JsonResponse:
    """Receive callbacks from the HumanityRules install Lambda after CloudFormation stack changes."""
    auth_header = request.headers.get("Authorization", "")
    expected_token = f"Bearer {settings.HUMR_API_SECRET_KEY}"

    if auth_header != expected_token:
        logger.error("AWS callback received with invalid authorization")
        return JsonResponse(
            {"error": "Invalid authorization"},
            status=401,
        )

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {"error": "Invalid JSON body"},
            status=400,
        )

    request_type = payload.get("request_type")
    aws_account_id = payload.get("aws_account")
    role_arn = payload.get("role_arn")
    external_id = payload.get("external_id")
    stack_region = payload.get("stack_region")

    if not external_id:
        return JsonResponse(
            {"error": "Missing external_id"},
            status=400,
        )

    try:
        uuid.UUID(external_id)
    except (ValueError, TypeError):
        logger.error("AWS callback received with invalid UUID: %s", external_id)
        return JsonResponse(
            {"error": "Invalid external_id format: must be a valid UUID"},
            status=400,
        )

    try:
        aws_account = AWSAccount.objects.get(external_id=external_id)
    except AWSAccount.DoesNotExist:
        logger.info("AWS callback received for unknown external_id: %s (ignored)", external_id)
        return JsonResponse({
            "status": "success",
            "message": "Codepath 200",
        })

    if request_type == "Create":
        aws_account.aws_account_id = aws_account_id or ""
        aws_account.role_arn = role_arn or ""
        aws_account.status = AWSAccount.Status.CONNECTED
        aws_account.status_message = f"Connected from {stack_region}"
        aws_account.save()

        logger.info(
            "AWS account connected: %s (org=%s, aws_id=%s)",
            aws_account.name,
            aws_account.organization.slug,
            aws_account_id,
        )

        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} connected",
        })

    elif request_type == "Update":
        aws_account.role_arn = role_arn or aws_account.role_arn
        aws_account.status_message = f"Updated from {stack_region}"
        aws_account.save()

        logger.info("AWS account updated: %s", aws_account.name)

        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} updated",
        })

    elif request_type == "Delete":
        aws_account.status = AWSAccount.Status.PENDING
        aws_account.role_arn = ""
        aws_account.status_message = "CloudFormation stack deleted by customer"
        aws_account.save()

        logger.info("AWS account disconnected: %s", aws_account.name)

        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} disconnected",
        })

    else:
        return JsonResponse(
            {"error": f"Unknown request_type: {request_type}"},
            status=400,
        )
