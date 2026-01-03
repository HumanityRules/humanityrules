"""
CloudFormation utility functions for deploying DevOpsHero infrastructure to customer accounts.
"""

from pathlib import Path

from botocore.exceptions import ClientError
from jinja2 import Environment, FileSystemLoader



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
    
    print(f"\n{'='*60}")
    print(f"📦 Deploying stack: {stack_name}")
    
    if template_path:
        print(f"   Template: {template_path.name}")
        with open(template_path) as f:
            template_body = f.read()
    else:
        print(f"   Template: (rendered from Jinja2)")
    
    stack_exists = False
    try:
        cf_client.describe_stacks(StackName=stack_name)
        stack_exists = True
        print(f"   Stack exists, will update")
    except ClientError as e:
        if "does not exist" in str(e):
            print(f"   Stack doesn't exist, will create")
        else:
            raise
    
    cf_parameters = []
    if parameters:
        for key, value in parameters.items():
            cf_parameters.append({"ParameterKey": key, "ParameterValue": value})
    
    cf_capabilities = capabilities or []
    
    try:
        if stack_exists:
            cf_client.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
            )
            waiter = cf_client.get_waiter("stack_update_complete")
        else:
            cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
                OnFailure="DELETE",  # Clean up on failure
            )
            waiter = cf_client.get_waiter("stack_create_complete")
        
        print(f"   ⏳ Waiting for stack operation to complete...")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 180})   # 180 * 10 seconds = 30 minutes
        
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
            # Try to get failure reason
            try:
                events = cf_client.describe_stack_events(StackName=stack_name)
                for event in events["StackEvents"][:5]:
                    if "FAILED" in event.get("ResourceStatus", ""):
                        print(f"      {event['LogicalResourceId']}: {event.get('ResourceStatusReason', 'Unknown')}")
            except:
                pass
            return False

