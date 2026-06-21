import json
import logging
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ...models import AWSAccount
from .. import base

logger = logging.getLogger(__name__)


@login_required
def integrations_root(request: HttpRequest) -> HttpResponse:
    """Redirect /integrations/ to the default tab (AWS accounts)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden
    return HttpResponseRedirect("/integrations/org/aws-accounts/")


@login_required
def integrations_org_aws_accounts(request: HttpRequest) -> HttpResponse:
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = "/integrations/org/aws-accounts/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    org = request.user.current_organization

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "aws-accounts"
    context["aws_accounts"] = AWSAccount.objects.filter(organization=org)

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
                response = HttpResponse("")
                response["HX-Trigger"] = f'{{"openCloudFormation": "{existing.get_cloudformation_url()}"}}'
                return response
            else:
                response = HttpResponse("")
                response["HX-Trigger"] = '{"validationError": "An AWS account with this name is already connected"}'
                return response

        aws_account = AWSAccount.objects.create(
            organization=org,
            name=name,
            created_by=request.user,
        )

        response = HttpResponse("")
        response["HX-Trigger"] = f'{{"openCloudFormation": "{aws_account.get_cloudformation_url()}"}}'
        return response

    return render(request, "humanityrules_app/integrations/aws_account_add_modal.html")


@csrf_exempt
@require_POST
def aws_install_account_callback(request: HttpRequest) -> JsonResponse:
    """Receive callbacks from the DevOpsHero install Lambda after CloudFormation stack changes."""
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
