"""
DevOpsHero Install Callback Lambda

CloudFormation Custom Resource handler that notifies the DevOpsHero backend
when customers deploy/update/delete the AWS account connection stack.
"""

import json
import os
import urllib3

http = urllib3.PoolManager()


def handler(event, context):
    """
    CloudFormation Custom Resource handler.
    Called when a customer deploys/updates/deletes the DevOpsHero stack.
    """
    print(f"Received event: {json.dumps(event)}")

    response_url = event["ResponseURL"]
    request_type = event["RequestType"]

    try:
        props = event["ResourceProperties"]
        aws_account = props.get("AwsAccount")
        role_arn = props.get("RoleArn")
        external_id = props.get("ExternalId")
        stack_region = props.get("StackRegion")

        api_endpoint = os.environ.get("DEVOPSHERO_API_ENDPOINT")
        api_secret = os.environ.get("API_SECRET_KEY")

        # Prepare payload for DevOpsHero backend
        payload = {
            "request_type": request_type,
            "aws_account": aws_account,
            "role_arn": role_arn,
            "external_id": external_id,
            "stack_region": stack_region,
        }

        # Call DevOpsHero backend API
        if api_endpoint:
            callback_url = f"{api_endpoint}/api/aws/account-callback"
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

            print(f"Backend response: {response.status} - {response.data.decode()}")

            if response.status >= 400:
                raise Exception(f"Backend API returned {response.status}")

        # Send SUCCESS response back to CloudFormation
        send_cfn_response(
            response_url,
            event,
            "SUCCESS",
            {"Message": f"Account {aws_account} {request_type.lower()}d successfully"},
        )

    except Exception as e:
        print(f"Error: {str(e)}")
        send_cfn_response(response_url, event, "FAILED", {"Message": str(e)})


def send_cfn_response(url, event, status, data):
    """Send response back to CloudFormation."""
    physical_id = event.get("PhysicalResourceId")
    if not physical_id:
        physical_id = f"devopshero-install-{event['ResourceProperties'].get('AwsAccount', 'unknown')}"

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

    print(f"Sending CFN response: {status}")
    http.request("PUT", url, body=body, headers={"Content-Type": ""})

