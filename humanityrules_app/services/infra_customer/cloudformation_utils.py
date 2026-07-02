"""
CloudFormation utility functions for deploying HumanityRules infrastructure to customer accounts.
"""

import logging
from pathlib import Path

from botocore.exceptions import ClientError, WaiterError
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


def stack_exists(cf_client, stack_name: str) -> bool:
    """Check if a CloudFormation stack exists."""
    try:
        logger.info("Checking if stack %(stack_name)s exists", {"stack_name": stack_name})
        cf_client.describe_stacks(StackName=stack_name)
        logger.info("Stack %(stack_name)s exists", {"stack_name": stack_name})
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            logger.info("Stack %(stack_name)s does not exist", {"stack_name": stack_name})
            return False
        raise


def get_stack_status(cf_client, stack_name: str) -> str | None:
    """Get the current status of a CloudFormation stack, or None if it doesn't exist."""
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        return response["Stacks"][0]["StackStatus"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            return None
        raise
    
    
def render_jinja2_template(template_path: Path, variables: dict) -> str:
    """Render a Jinja2 template with the given variables."""
    env = Environment(
        loader=FileSystemLoader(template_path.parent),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path.name)
    return template.render(**variables)


def deploy_cloudformation_stack(
    cf_client,
    stack_name: str,
    template_path: Path | None,
    template_body: str | None,
    capabilities: list[str] | None,
    parameters: dict | None,
) -> bool:
    """
    Deploy or update a CloudFormation stack.
    
    Args:
        cf_client: CloudFormation client
        stack_name: Name of the stack
        template_path: Path to a JSON template file (mutually exclusive with template_body)
        template_body: Already-rendered template string (mutually exclusive with template_path)
        capabilities: List of IAM capabilities (e.g., ["CAPABILITY_NAMED_IAM"])
        parameters: Dict of CloudFormation parameters
    
    Returns True if successful, False otherwise.
    """
    if template_path and template_body:
        raise ValueError("Cannot specify both template_path and template_body")
    if not template_path and not template_body:
        raise ValueError("Must specify either template_path or template_body")
    
    logger.info("%(separator)s", {"separator": "=" * 60})
    logger.info("Deploying stack: %(stack_name)s", {"stack_name": stack_name})
    
    if template_path:
        logger.info("   Template: %(template_name)s", {"template_name": template_path.name})
        with open(template_path) as f:
            template_body = f.read()
    else:
        logger.info("   Template: (rendered from Jinja2)")
        
    cf_parameters = []
    if parameters:
        for key, value in parameters.items():
            cf_parameters.append({"ParameterKey": key, "ParameterValue": value})
    
    cf_capabilities = capabilities or []
    
    logger.info("   Validating template")
    try:
        cf_client.validate_template(TemplateBody=template_body)
    except ClientError as e:
        logger.error("   Template validation failed: %(error)s", {"error": e.response["Error"]["Message"]})
        return False
    
    try:
        if stack_exists(cf_client=cf_client, stack_name=stack_name):
            logger.info("   Stack exists, updating")
            cf_client.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
                DeploymentConfig={"Mode": "EXPRESS"},
            )
            waiter = cf_client.get_waiter("stack_update_complete")
        else:
            logger.info("   Stack does not exist, creating")
            # Express mode: complete once config is applied, rollback disabled by default.
            # OnFailure is omitted — it conflicts with Express, which owns failure handling.
            cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
                DeploymentConfig={"Mode": "EXPRESS"},
            )
            waiter = cf_client.get_waiter("stack_create_complete")
        
        logger.info("   Waiting for stack operation to complete")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})   # 60 * 10 seconds = 10 minutes
        
        # Get stack outputs just to print them
        response = cf_client.describe_stacks(StackName=stack_name)
        stack = response["Stacks"][0]
        
        logger.info("   Stack %(stack_status)s", {"stack_status": stack["StackStatus"]})
        
        if stack.get("Outputs"):
            logger.info("   Outputs:")
            for output in stack["Outputs"]:
                logger.info("      %(output_key)s: %(output_value)s", {"output_key": output["OutputKey"], "output_value": output["OutputValue"]})
        
        return True
        
    except ClientError as e:
        error_message = str(e)
        if "No updates are to be performed" in error_message:
            logger.info("   No updates needed")
            return True
        logger.error("   Failed: %(error)s", {"error": str(e)})
        _print_stack_failure_events(cf_client=cf_client, stack_name=stack_name)
        return False
    except WaiterError:
        # Stack failed - get status and events
        try:
            response = cf_client.describe_stacks(StackName=stack_name)
            stack_status = response["Stacks"][0]["StackStatus"]
            logger.error("   Stack failed with status: %(stack_status)s", {"stack_status": stack_status})
        except ClientError:
            logger.error("   Stack creation failed (stack was deleted)")
            return False
        
        _print_stack_failure_events(cf_client=cf_client, stack_name=stack_name)
        
        if stack_status in ["ROLLBACK_COMPLETE", "CREATE_FAILED"]:
            logger.info("\n   To retry, first delete the failed stack:")
            logger.info("      aws cloudformation delete-stack --stack-name %(stack_name)s", {"stack_name": stack_name})
        
        return False


def _print_stack_failure_events(cf_client, stack_name: str) -> None:
    """Print recent failure events from a CloudFormation stack."""
    try:
        events = cf_client.describe_stack_events(StackName=stack_name)
        logger.error("   Recent failure events:")
        for event in events["StackEvents"][:10]:
            status = event.get("ResourceStatus", "")
            if "FAILED" in status or "ROLLBACK" in status:
                reason = event.get("ResourceStatusReason", "")
                logger.error(
                    "      %(logical_id)s (%(status)s): %(reason)s",
                    {"logical_id": event["LogicalResourceId"], "status": status, "reason": reason},
                )
    except ClientError:
        logger.error("   (Could not retrieve stack events - stack may have been deleted)")


def cleanup_rollback_complete_stacks(cf_client, stack_names: list[str]) -> None:
    """Delete any ROLLBACK_COMPLETE stacks left by a previous failed provisioning attempt.

    CloudFormation stacks that fail during creation end up in ROLLBACK_COMPLETE — they
    still "exist" but are unusable. This must run before deploying so that CDK can
    create fresh stacks with the same names.
    """
    for stack_name in stack_names:
        status = get_stack_status(cf_client, stack_name)
        if status != "ROLLBACK_COMPLETE":
            continue

        logger.info("Stack '%(stack_name)s' is in ROLLBACK_COMPLETE, deleting before retry", {"stack_name": stack_name})
        deleted = delete_stack_and_wait(cf_client, stack_name)
        if not deleted:
            raise RuntimeError(f"Could not delete failed stack '{stack_name}'")


def delete_stack_and_wait(cf_client, stack_name: str) -> bool:
    """
    Delete a CloudFormation stack and wait for completion.
    
    Returns True on success, False on failure.
    """
    if not stack_exists(cf_client, stack_name):
        logger.info("   Stack '%(stack_name)s' does not exist, skipping", {"stack_name": stack_name})
        return True
    
    logger.info("   Deleting stack '%(stack_name)s'", {"stack_name": stack_name})
    try:
        cf_client.delete_stack(StackName=stack_name, DeploymentConfig={"Mode": "EXPRESS"})
        
        # Wait for deletion
        waiter = cf_client.get_waiter("stack_delete_complete")
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 10, "MaxAttempts": 60},  # 10 minutes max
        )
        logger.info("   Stack '%(stack_name)s' deleted", {"stack_name": stack_name})
        return True
    except (ClientError, WaiterError) as e:
        # WaiterError covers DELETE_FAILED and credential invalidation mid-delete (e.g. a stack
        # that deletes the very IAM role whose session is performing the delete) — surface as a
        # failed delete rather than letting it propagate and crash the caller.
        logger.error("   Failed to delete stack '%(stack_name)s': %(error)s", {"stack_name": stack_name, "error": str(e)})
        return False


def list_stacks_by_prefix(cf_client, prefix: str) -> list[str]:
    """Return the names of all non-deleted stacks whose name starts with ``prefix``.

    ``list_stacks`` includes DELETE_COMPLETE stacks for 90 days after deletion, so we
    filter those out. Everything else (including DELETE_FAILED, DELETE_IN_PROGRESS,
    and transient states) is treated as "still present".
    """
    stack_names: list[str] = []
    paginator = cf_client.get_paginator("list_stacks")
    for page in paginator.paginate():
        for summary in page.get("StackSummaries", []):
            if summary.get("StackStatus") == "DELETE_COMPLETE":
                continue
            name = summary["StackName"]
            if name.startswith(prefix):
                stack_names.append(name)
    return stack_names


def get_stack_output(cf_client, stack_name: str, output_key: str) -> str | None:
    """Get a specific output value from a CloudFormation stack."""
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        stack = response["Stacks"][0]
        for output in stack.get("Outputs", []):
            if output["OutputKey"] == output_key:
                return output["OutputValue"]
    except ClientError:
        pass
    return None


def get_app_urls(cf_client, app_name: str, env_slug: str, has_domain: bool) -> dict[str, str | None]:
    """Get the app URLs from CloudFormation stack outputs."""
    stack_name = f"humr-{env_slug}-{app_name}-app"

    shared_alb_dns = get_stack_output(cf_client, stack_name=stack_name, output_key="SharedAlbDns")
    alb_url = f"http://{shared_alb_dns}" if shared_alb_dns else None

    urls = {"alb_url": alb_url}

    if has_domain:
        urls["https_url"] = get_stack_output(cf_client, stack_name=stack_name, output_key="HttpsUrl")

    return urls


def print_deployment_summary(
    account_id: str,
    region: str,
    app_name: str,
    image_tag: str,
    service_url: str,
    alb_dns: str,
) -> None:
    """Print a deployment summary with app URLs."""
    logger.info("%(separator)s", {"separator": "\n" + "=" * 60})
    logger.info("Deployment complete")
    logger.info("%(separator)s", {"separator": "=" * 60})
    logger.info("Account: %(account_id)s", {"account_id": account_id})
    logger.info("Region:  %(region)s", {"region": region})

    logger.info("App: %(app_name)s", {"app_name": app_name})
    logger.info("   Image tag: %(image_tag)s", {"image_tag": image_tag})

    if service_url.startswith("https://"):
        logger.info("App URL (HTTPS): %(url)s", {"url": service_url})

    if alb_dns:
        logger.info("App URL (ALB):   http://%(alb_dns)s", {"alb_dns": alb_dns})

