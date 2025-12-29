#!/usr/bin/env python3
"""
Test script to deploy DevOpsHero infrastructure to a customer account.

This script:
1. Assumes the cross-account role into the target AWS account
2. Deploys the VPC CloudFormation stack
3. Deploys the ECS Cluster CloudFormation stack

Usage:
    cd infra_customer
    uv run python test_deploy_infra.py

Required environment variables (from ../.env):
    DOH_AWS_ACCESS_KEY - DevOpsHero control plane AWS access key
    DOH_AWS_SECRET_KEY - DevOpsHero control plane AWS secret key
"""

import ipaddress
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"

# Stack names
VPC_STACK_NAME = "devopshero-vpc"
ECS_STACK_NAME = "devopshero-ecs-cluster"


def cidrs_overlap(cidr1: str, cidr2: str) -> bool:
    """Check if two CIDR blocks overlap."""
    net1 = ipaddress.ip_network(cidr1, strict=False)
    net2 = ipaddress.ip_network(cidr2, strict=False)
    return net1.overlaps(net2)


def find_available_vpc_cidr(ec2_client) -> dict:
    """
    Find an available /20 CIDR in the 172.16-31.x.x range that doesn't 
    conflict with existing VPCs.
    
    Returns a dict with VpcCidr, PublicSubnet1Cidr, PublicSubnet2Cidr.
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
                # /20 gives us 4096 IPs, we'll carve out two /24 subnets
                vpc_net = ipaddress.ip_network(candidate)
                subnets = list(vpc_net.subnets(new_prefix=24))
                
                result = {
                    "VpcCidr": candidate,
                    "PublicSubnet1Cidr": str(subnets[0]),
                    "PublicSubnet2Cidr": str(subnets[1]),
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
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        
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


def main():
    print("🚀 DevOpsHero Infrastructure Deployment Test")
    print("=" * 60)
    
    # Load environment
    load_env()
        
    # Assume role into target account
    session = get_assumed_role_session(
        access_key=os.getenv("DOH_AWS_ACCESS_KEY"),
        secret_key=os.getenv("DOH_AWS_SECRET_KEY"),
        account_id=TARGET_ACCOUNT_ID,
        external_id=TARGET_EXTERNAL_ID,
        region=TARGET_REGION,
    )
    
    # Create AWS clients
    cf_client = session.client("cloudformation")
    ec2_client = session.client("ec2")
    
    # Get template paths
    template_dir = Path(__file__).parent
    vpc_template = template_dir / "cf_vpc.json"
    ecs_template = template_dir / "cf_ecs_cluster.json"
    
    # Verify templates exist
    for template in [vpc_template, ecs_template]:
        if not template.exists():
            print(f"❌ Template not found: {template}")
            sys.exit(1)
    
    # Find available CIDR range (avoids conflicts with existing VPCs)
    vpc_params = find_available_vpc_cidr(ec2_client)
    
    # Deploy VPC first (ECS cluster depends on it)
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=VPC_STACK_NAME,
        template_path=vpc_template,
        parameters=vpc_params,
    )
    
    if not success:
        print("\n❌ VPC deployment failed. Stopping.")
        sys.exit(1)
    
    # Deploy ECS Cluster (depends on VPC exports)
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=ECS_STACK_NAME,
        template_path=ecs_template,
        capabilities=["CAPABILITY_NAMED_IAM"],
    )
    
    if not success:
        print("\n❌ ECS Cluster deployment failed.")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("🎉 Infrastructure deployment complete!")
    print("=" * 60)
    print(f"\nAccount: {TARGET_ACCOUNT_ID}")
    print(f"Region:  {TARGET_REGION}")
    print(f"VPC CIDR: {vpc_params['VpcCidr']}")
    print(f"\nNext steps:")
    print("  1. Build and push your Docker image to ECR")
    print("  2. Deploy an app using the ECS cluster")


if __name__ == "__main__":
    main()

