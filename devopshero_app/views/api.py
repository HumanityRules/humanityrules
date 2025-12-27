"""
API endpoints for external integrations.
These endpoints are called by AWS Lambda and other services, not by browsers.
"""

import json
import logging
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import AWSAccount

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def aws_install_account_callback(request):
    """
    Receive callbacks from the DevOpsHero install Lambda.
    
    Called when a customer deploys/updates/deletes the CloudFormation stack
    in their AWS account. The Lambda sends:
    - request_type: "Create", "Update", or "Delete"
    - aws_account: The 12-digit AWS account ID
    - role_arn: The IAM role ARN that DevOpsHero can assume
    - external_id: The UUID that identifies which AWSAccount record this is for
    - stack_region: The AWS region where the stack was deployed
    """
    # Validate Bearer token
    auth_header = request.headers.get("Authorization", "")
    expected_token = f"Bearer {settings.DOH_API_SECRET_KEY}"
    
    if auth_header != expected_token:
        logger.warning("AWS callback received with invalid authorization")
        return JsonResponse(
            {"error": "Invalid authorization"},
            status=401,
        )
    
    # Parse request body
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {"error": "Invalid JSON body"},
            status=400,
        )
    
    # Extract fields
    request_type = payload.get("request_type")
    aws_account_id = payload.get("aws_account")
    role_arn = payload.get("role_arn")
    external_id = payload.get("external_id")
    stack_region = payload.get("stack_region")
    
    # Validate required fields
    if not external_id:
        return JsonResponse(
            {"error": "Missing external_id"},
            status=400,
        )
    
    # Validate external_id is a valid UUID
    try:
        uuid.UUID(external_id)
    except (ValueError, TypeError):
        logger.warning(f"AWS callback received with invalid UUID: {external_id}")
        return JsonResponse(
            {"error": f"Invalid external_id format: must be a valid UUID"},
            status=400,
        )
    
    # Find the AWSAccount record by external_id
    try:
        aws_account = AWSAccount.objects.get(external_id=external_id)
    except AWSAccount.DoesNotExist:
        logger.warning(f"AWS callback received for unknown external_id: {external_id}")
        return JsonResponse(
            {"error": "Unknown external_id"},
            status=404,
        )
    
    # Handle different request types
    if request_type == "Create":
        aws_account.aws_account_id = aws_account_id or ""
        aws_account.role_arn = role_arn or ""
        aws_account.status = AWSAccount.Status.CONNECTED
        aws_account.status_message = f"Connected from {stack_region}"
        aws_account.save()
        
        logger.info(
            f"AWS account connected: {aws_account.name} "
            f"(org={aws_account.organization.slug}, aws_id={aws_account_id})"
        )
        
        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} connected",
        })
    
    elif request_type == "Update":
        # Stack was updated - refresh the role ARN in case it changed
        aws_account.role_arn = role_arn or aws_account.role_arn
        aws_account.status_message = f"Updated from {stack_region}"
        aws_account.save()
        
        logger.info(f"AWS account updated: {aws_account.name}")
        
        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} updated",
        })
    
    elif request_type == "Delete":
        # Customer deleted the CloudFormation stack - mark as disconnected
        aws_account.status = AWSAccount.Status.PENDING
        aws_account.role_arn = ""
        aws_account.status_message = "CloudFormation stack deleted by customer"
        aws_account.save()
        
        logger.info(f"AWS account disconnected: {aws_account.name}")
        
        return JsonResponse({
            "status": "success",
            "message": f"Account {aws_account_id} disconnected",
        })
    
    else:
        return JsonResponse(
            {"error": f"Unknown request_type: {request_type}"},
            status=400,
        )

