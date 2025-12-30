"""
Utility functions for deploying DevOpsHero infrastructure to customer accounts.
"""

import ipaddress
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def cidrs_overlap(cidr1: str, cidr2: str) -> bool:
    """Check if two CIDR blocks overlap."""
    net1 = ipaddress.ip_network(cidr1, strict=False)
    net2 = ipaddress.ip_network(cidr2, strict=False)
    return net1.overlaps(net2)


def find_available_vpc_cidr(ec2_client) -> dict:
    """
    Find an available /20 CIDR in the 172.16-31.x.x range that doesn't 
    conflict with existing VPCs.
    
    Returns a dict with VpcCidr, PublicSubnet1Cidr, PublicSubnet2Cidr,
    PrivateSubnet1Cidr, PrivateSubnet2Cidr.
    """
    print("\n🔍 Scanning for available CIDR range...")
    
    # Get all existing VPC CIDRs in the account
    vpcs = ec2_client.describe_vpcs()
    used_cidrs = []
    for vpc in vpcs["Vpcs"]:
        used_cidrs.append(vpc["CidrBlock"])
        # Also check associated CIDR blocks (VPCs can have multiple)
        for assoc in vpc.get("CidrBlockAssociationSet", []):
            if assoc.get("CidrBlock"):
                used_cidrs.append(assoc["CidrBlock"])
    
    if used_cidrs:
        print(f"   Found existing VPC CIDRs: {', '.join(used_cidrs)}")
    else:
        print("   No existing VPCs found")
    
    # Try /20 blocks in 172.16.0.0/12 range (172.16.0.0 - 172.31.255.255)
    # We'll try 172.20.0.0/20, 172.20.16.0/20, 172.20.32.0/20, etc.
    # Then 172.21.x.x, 172.22.x.x, up to 172.31.x.x
    for second_octet in range(20, 32):  # 172.20 through 172.31
        for third_octet in range(0, 256, 16):  # /20 = 16 in third octet
            candidate = f"172.{second_octet}.{third_octet}.0/20"
            
            # Check for overlap with any existing CIDR
            has_conflict = False
            for used in used_cidrs:
                if cidrs_overlap(candidate, used):
                    has_conflict = True
                    break
            
            if not has_conflict:
                # Found a good one! Calculate subnet CIDRs
                # /20 gives us 4096 IPs, we'll carve out four /24 subnets:
                # - 2 public (for NAT Gateway and potential ALB)
                # - 2 private (for Fargate tasks)
                vpc_net = ipaddress.ip_network(candidate)
                subnets = list(vpc_net.subnets(new_prefix=24))
                
                result = {
                    "VpcCidr": candidate,
                    "PublicSubnet1Cidr": str(subnets[0]),   # .0.0/24
                    "PublicSubnet2Cidr": str(subnets[1]),   # .1.0/24
                    "PrivateSubnet1Cidr": str(subnets[2]),  # .2.0/24
                    "PrivateSubnet2Cidr": str(subnets[3]),  # .3.0/24
                }
                print(f"   ✅ Selected: {candidate}")
                return result
    
    raise Exception("No available CIDR range found in 172.16-31.x.x")


def load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        sys.exit(1)
    load_dotenv(env_path)
    
    required_vars = ["DOH_AWS_ACCESS_KEY", "DOH_AWS_SECRET_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


def get_assumed_role_session(
    access_key: str,
    secret_key: str,
    account_id: str,
    external_id: str,
    region: str,
) -> boto3.Session:
    """
    Assume the DevOpsHero role in the target account and return a boto3 session.
    """
    # Create STS client with control plane credentials
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    
    # The role ARN follows the pattern from cf_install_template.json
    role_arn = f"arn:aws:iam::{account_id}:role/devopshero-{external_id}"
    
    print(f"🔑 Assuming role: {role_arn}")
    
    try:
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="devopshero-infra-deployment",
            ExternalId=external_id,
            DurationSeconds=3600,  # 1 hour
        )
    except ClientError as e:
        print(f"❌ Failed to assume role: {e}")
        sys.exit(1)
    
    credentials = response["Credentials"]
    
    # Create a new session with the assumed role credentials
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )
    
    # Verify we're in the right account
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"✅ Assumed role in account: {identity['Account']}")
    
    return session


def deploy_cloudformation_stack(
    cf_client,
    stack_name: str,
    template_path: Path,
    capabilities: list[str] | None = None,
    parameters: dict | None = None,
) -> bool:
    """
    Deploy or update a CloudFormation stack.
    
    Returns True if successful, False otherwise.
    """
    print(f"\n{'='*60}")
    print(f"📦 Deploying stack: {stack_name}")
    print(f"   Template: {template_path.name}")
    
    # Read the template
    with open(template_path) as f:
        template_body = f.read()
    
    # Check if stack exists
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
    
    # Prepare parameters
    cf_parameters = []
    if parameters:
        for key, value in parameters.items():
            cf_parameters.append({"ParameterKey": key, "ParameterValue": value})
    
    # Prepare capabilities
    cf_capabilities = capabilities or []
    
    try:
        if stack_exists:
            # Update stack
            cf_client.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=cf_parameters,
                Capabilities=cf_capabilities,
            )
            waiter = cf_client.get_waiter("stack_update_complete")
        else:
            # Create stack
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
        
        # Get stack outputs
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

