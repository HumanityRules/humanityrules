"""
CloudFormation utility functions for deploying DevOpsHero infrastructure to customer accounts.
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
            )
            waiter = cf_client.get_waiter("stack_update_complete")
        else:
            logger.info("   Stack does not exist, creating")
            cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
                OnFailure="ROLLBACK",  # Keep stack around so we can see failure events
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
        cf_client.delete_stack(StackName=stack_name)
        
        # Wait for deletion
        waiter = cf_client.get_waiter("stack_delete_complete")
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 10, "MaxAttempts": 60},  # 10 minutes max
        )
        logger.info("   Stack '%(stack_name)s' deleted", {"stack_name": stack_name})
        return True
    except ClientError as e:
        logger.error("   Failed to delete stack '%(stack_name)s': %(error)s", {"stack_name": stack_name, "error": str(e)})
        return False


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
    stack_name = f"doh-{env_slug}-{app_name}-app"

    urls = {
        "alb_url": get_stack_output(cf_client, stack_name=stack_name, output_key="AlbUrl"),
    }

    if has_domain:
        urls["https_url"] = get_stack_output(cf_client, stack_name=stack_name, output_key="HttpsUrl")

    return urls


def print_deployment_summary(
    cf_client,
    account_id: str,
    region: str,
    app_name: str,
    env_slug: str,
    image_tag: str,
    has_domain: bool,
    cluster_name: str,
) -> None:
    """Print a deployment summary with app URLs and ECS exec instructions."""
    logger.info("%(separator)s", {"separator": "\n" + "=" * 60})
    logger.info("Deployment complete")
    logger.info("%(separator)s", {"separator": "=" * 60})
    logger.info("Account: %(account_id)s", {"account_id": account_id})
    logger.info("Region:  %(region)s", {"region": region})

    logger.info("App: %(app_name)s", {"app_name": app_name})
    logger.info("   Image tag: %(image_tag)s", {"image_tag": image_tag})

    urls = get_app_urls(
        cf_client=cf_client,
        app_name=app_name,
        env_slug=env_slug,
        has_domain=has_domain,
    )

    if urls.get("https_url"):
        logger.info("App URL (HTTPS): %(https_url)s", {"https_url": urls["https_url"]})

    if urls.get("alb_url"):
        logger.info("App URL (ALB):   %(alb_url)s", {"alb_url": urls["alb_url"]})
    else:
        logger.info("URLs: (waiting for ALB to be ready...)")

