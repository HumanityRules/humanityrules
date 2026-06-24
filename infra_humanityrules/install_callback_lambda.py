"""
HumanityRules Install Callback Lambda

CloudFormation Custom Resource handler that notifies the HumanityRules backend
when customers deploy/update/delete the AWS account connection stack.
"""

import json
import logging
import os
import urllib3

# Configure logging for Lambda (sends to CloudWatch)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

http = urllib3.PoolManager()


def resolve_api_endpoint(requested):
    """Pick the control-plane endpoint to call back, guarding the shared secret.

    The customer's stack passes its issuing control plane's URL as ApiEndpoint. We only
    honour it when it is allow-listed (HUMR_API_ALLOWED_ENDPOINTS, plus the Lambda's own
    default), so a tampered template parameter can't redirect our bearer token to an
    attacker-controlled host. Anything missing or unrecognised falls back to the default.
    """
    default_endpoint = os.environ.get("HUMR_API_ENDPOINT")
    allowed = {e.strip() for e in os.environ.get("HUMR_API_ALLOWED_ENDPOINTS", "").split(",") if e.strip()}
    if default_endpoint:
        allowed.add(default_endpoint)

    if requested and requested in allowed:
        return requested
    if requested:
        logger.error(f"Requested ApiEndpoint not allow-listed, using default: {requested}")
    return default_endpoint


def handler(event, context):
    """
    CloudFormation Custom Resource handler.
    Called when a customer deploys/updates/deletes the HumanityRules stack.
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    response_url = event["ResponseURL"]
    request_type = event["RequestType"]

    try:
        props = event["ResourceProperties"]
        aws_account = props.get("AwsAccount")
        role_arn = props.get("RoleArn")
        external_id = props.get("ExternalId")
        stack_region = props.get("StackRegion")

        api_endpoint = resolve_api_endpoint(props.get("ApiEndpoint"))
        api_secret = os.environ.get("HUMR_API_SECRET_KEY")

        # Prepare payload for HumanityRules backend
        payload = {
            "request_type": request_type,
            "aws_account": aws_account,
            "role_arn": role_arn,
            "external_id": external_id,
            "stack_region": stack_region,
        }

        # Call HumanityRules backend API
        if api_endpoint:
            callback_url = f"{api_endpoint}/api/aws/install-account-callback"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_secret}",
            }

            response = http.request(
                "POST",
                callback_url,
                body=json.dumps(payload),
                headers=headers,
            )

            logger.info(f"Backend response: {response.status} - {response.data.decode()}")

            if response.status >= 400:
                raise Exception(f"Backend API returned {response.status}")
        else:
            logger.error("No HUMR_API_ENDPOINT configured, skipping backend callback")

        # Send SUCCESS response back to CloudFormation
        send_cfn_response(
            response_url,
            event,
            "SUCCESS",
            {"Message": f"Account {aws_account} {request_type.lower()}d successfully"},
        )
        return {"status": "success", "aws_account": aws_account}

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        send_cfn_response(response_url, event, "FAILED", {"Message": str(e)})
        return {"status": "error", "message": str(e)}


def send_cfn_response(url, event, status, data):
    """
    Send response back to CloudFormation.
    
    CloudFormation Custom Resources wait for this response to know if the
    operation succeeded or failed. Without it, the stack hangs for 1 hour.
    """
    physical_id = event.get("PhysicalResourceId")
    if not physical_id:
        physical_id = f"humr-install-{event['ResourceProperties'].get('AwsAccount', 'unknown')}"

    body = json.dumps(
        {
            "Status": status,
            "Reason": data.get("Message", "See CloudWatch logs"),
            "PhysicalResourceId": physical_id,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": data,
        }
    )

    logger.info(f"Sending CFN response: {status}")
    http.request("PUT", url, body=body, headers={"Content-Type": ""})

