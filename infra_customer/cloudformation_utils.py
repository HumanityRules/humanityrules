"""
CloudFormation utility functions for deploying DevOpsHero infrastructure to customer accounts.
"""

from pathlib import Path

from botocore.exceptions import ClientError, WaiterError
from jinja2 import Environment, FileSystemLoader


def stack_exists(cf_client, stack_name: str) -> bool:
    """Check if a CloudFormation stack exists."""
    try:
        print(f"Checking if stack {stack_name} exists...")
        cf_client.describe_stacks(StackName=stack_name)
        print(f"Stack {stack_name} exists.")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            print(f"Stack {stack_name} does not exist.")
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
    
    print(f"{'='*60}")
    print(f"📦 Deploying stack: {stack_name}")
    
    if template_path:
        print(f"   Template: {template_path.name}")
        with open(template_path) as f:
            template_body = f.read()
    else:
        print(f"   Template: (rendered from Jinja2)")
        
    cf_parameters = []
    if parameters:
        for key, value in parameters.items():
            cf_parameters.append({"ParameterKey": key, "ParameterValue": value})
    
    cf_capabilities = capabilities or []
    
    print(f"   Validating template...")
    try:
        cf_client.validate_template(TemplateBody=template_body)
    except ClientError as e:
        print(f"   ❌ Template validation failed: {e.response['Error']['Message']}")
        return False
    
    try:
        if stack_exists(cf_client=cf_client, stack_name=stack_name):
            print(f"   Stack exists, updating...")
            cf_client.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
            )
            waiter = cf_client.get_waiter("stack_update_complete")
        else:
            print(f"   Stack does not exist, creating...")
            cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
                OnFailure="ROLLBACK",  # Keep stack around so we can see failure events
            )
            waiter = cf_client.get_waiter("stack_create_complete")
        
        print(f"   ⏳ Waiting for stack operation to complete...")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})   # 60 * 10 seconds = 10 minutes
        
        # Get stack outputs just to print them
        response = cf_client.describe_stacks(StackName=stack_name)
        stack = response["Stacks"][0]
        
        print(f"   ✅ Stack {stack['StackStatus']}")
        
        if stack.get("Outputs"):
            print(f"   Outputs:")
            for output in stack["Outputs"]:
                print(f"      {output['OutputKey']}: {output['OutputValue']}")
        
        return True
        
    except ClientError as e:
        error_message = str(e)
        if "No updates are to be performed" in error_message:
            print(f"   ℹ️  No updates needed")
            return True
        else:
            print(f"   ❌ Failed: {e}")
            _print_stack_failure_events(cf_client=cf_client, stack_name=stack_name)
            return False
    except WaiterError:
        # Stack failed - get status and events
        try:
            response = cf_client.describe_stacks(StackName=stack_name)
            stack_status = response["Stacks"][0]["StackStatus"]
            print(f"   ❌ Stack failed with status: {stack_status}")
        except ClientError:
            print(f"   ❌ Stack creation failed (stack was deleted)")
            return False
        
        _print_stack_failure_events(cf_client=cf_client, stack_name=stack_name)
        
        if stack_status in ["ROLLBACK_COMPLETE", "CREATE_FAILED"]:
            print(f"\n   💡 To retry, first delete the failed stack:")
            print(f"      aws cloudformation delete-stack --stack-name {stack_name}")
        
        return False


def _print_stack_failure_events(cf_client, stack_name: str) -> None:
    """Print recent failure events from a CloudFormation stack."""
    try:
        events = cf_client.describe_stack_events(StackName=stack_name)
        print(f"   Recent failure events:")
        for event in events["StackEvents"][:10]:
            status = event.get("ResourceStatus", "")
            if "FAILED" in status or "ROLLBACK" in status:
                reason = event.get("ResourceStatusReason", "")
                print(f"      {event['LogicalResourceId']} ({status}): {reason}")
    except ClientError:
        print(f"   (Could not retrieve stack events - stack may have been deleted)")


def delete_stack_and_wait(cf_client, stack_name: str) -> bool:
    """
    Delete a CloudFormation stack and wait for completion.
    
    Returns True on success, False on failure.
    """
    if not stack_exists(cf_client, stack_name):
        print(f"   ⏭️  Stack '{stack_name}' does not exist, skipping")
        return True
    
    print(f"   🗑️  Deleting stack '{stack_name}'...")
    try:
        cf_client.delete_stack(StackName=stack_name)
        
        # Wait for deletion
        waiter = cf_client.get_waiter("stack_delete_complete")
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 10, "MaxAttempts": 60},  # 10 minutes max
        )
        print(f"   ✅ Stack '{stack_name}' deleted")
        return True
    except ClientError as e:
        print(f"   ❌ Failed to delete stack '{stack_name}': {e}")
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


def get_app_urls(cf_client, app_name: str, has_domain: bool) -> dict[str, str | None]:
    """Get the app URLs from CloudFormation stack outputs."""
    stack_name = f"doh-app-with-alb-{app_name}"

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
    image_tag: str,
    has_domain: bool,
    cluster_name: str,
) -> None:
    """Print a deployment summary with app URLs and ECS exec instructions."""
    print("\n" + "=" * 60)
    print("🎉 Deployment complete!")
    print("=" * 60)
    print(f"\nAccount: {account_id}")
    print(f"Region:  {region}")

    print(f"\n📊 App: {app_name}")
    print(f"   Image tag: {image_tag}")

    urls = get_app_urls(
        cf_client=cf_client,
        app_name=app_name,
        has_domain=has_domain,
    )

    if urls.get("https_url"):
        print(f"\n🔒 App URL (HTTPS): {urls['https_url']}")

    if urls.get("alb_url"):
        print(f"🌐 App URL (ALB):   {urls['alb_url']}")
    else:
        print("\n   URLs: (waiting for ALB to be ready...)")

    print(f"\n   Or use ECS Exec to connect to the container:")
    print(f"   aws ecs execute-command --cluster {cluster_name} \\")
    print(f"       --task <task-id> --container {app_name} \\")
    print(f"       --interactive --command /bin/sh")

