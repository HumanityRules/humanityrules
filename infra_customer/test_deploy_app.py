#!/usr/bin/env python3
"""
Test script to deploy DevOpsHero infrastructure to a customer account.

This script:
1. Assumes the cross-account role into the target AWS account
2. Deploys the VPC CloudFormation stack
3. Deploys the ECS Cluster CloudFormation stack
4. Builds and pushes Docker image to ECR
5. Deploys app to ECS Fargate with ALB (or without ALB if --no-alb)

Usage:
    cd infra_customer
    uv run python test_deploy_app.py              # Deploy infra + app with ALB
    uv run python test_deploy_app.py --infra-only # Deploy only VPC + ECS cluster
    uv run python test_deploy_app.py --app-only   # Deploy only app (assumes infra exists)
    uv run python test_deploy_app.py --no-alb     # Deploy without ALB (VPN/bastion access only)

Required environment variables (from ../.env):
    DOH_AWS_ACCESS_KEY - DevOpsHero control plane AWS access key
    DOH_AWS_SECRET_KEY - DevOpsHero control plane AWS secret key
"""

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from deployment_utils import (
    cidrs_overlap,
    deploy_cloudformation_stack,
    find_available_vpc_cidr,
    get_assumed_role_session,
    load_env,
)

# Configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"

# Stack names
VPC_STACK_NAME = "devopshero-vpc"
ECS_STACK_NAME = "devopshero-ecs-cluster"
APP_STACK_NAME = "devopshero-app-definition"
ALB_STACK_NAME = "devopshero-app-with-alb"
NO_ALB_STACK_NAME = "devopshero-app-no-alb"

# App paths
SIMPLE_DASHBOARD_PATH = Path(__file__).parent.parent / "deployable_repos" / "simple_dashboard"
ECR_REPO_NAME = "devopshero/simple-dashboard"


def build_and_push_docker_image(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_path: Path,
    repo_name: str,
    image_tag: str,
) -> str:
    """
    Build Docker image and push to ECR.
    
    Returns the full image URI.
    """
    print(f"\n{'='*60}")
    print(f"🐳 Building and pushing Docker image")
    print(f"   App path: {app_path}")
    print(f"   Repository: {repo_name}")
    print(f"   Tag: {image_tag}")
    
    # Get ECR login credentials
    ecr_client = session.client("ecr")
    
    try:
        auth_response = ecr_client.get_authorization_token()
        auth_data = auth_response["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":")
        registry_url = auth_data["proxyEndpoint"]
        
        print(f"   ✅ Got ECR authorization token")
    except ClientError as e:
        print(f"   ❌ Failed to get ECR auth token: {e}")
        return None
    
    # Full image URI
    image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}:{image_tag}"
    
    # Build Docker image for AMD64 (Fargate runs on x86_64, not ARM)
    print(f"   ⏳ Building Docker image (platform: linux/amd64)...")
    build_result = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", image_uri, "."],
        cwd=app_path,
        capture_output=True,
        text=True,
    )
    
    if build_result.returncode != 0:
        print(f"   ❌ Docker build failed:")
        print(build_result.stderr)
        return None
    
    print(f"   ✅ Docker image built: {image_uri}")
    
    # Login to ECR
    print(f"   ⏳ Logging into ECR...")
    login_result = subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry_url],
        input=password,
        capture_output=True,
        text=True,
    )
    
    if login_result.returncode != 0:
        print(f"   ❌ ECR login failed:")
        print(login_result.stderr)
        return None
    
    print(f"   ✅ Logged into ECR")
    
    # Push image
    print(f"   ⏳ Pushing image to ECR...")
    push_result = subprocess.run(
        ["docker", "push", image_uri],
        capture_output=True,
        text=True,
    )
    
    if push_result.returncode != 0:
        print(f"   ❌ Docker push failed:")
        print(push_result.stderr)
        return None
    
    print(f"   ✅ Image pushed to ECR: {image_uri}")
    
    return image_uri


def deploy_infrastructure(
    cf_client,
    ec2_client,
    template_dir: Path,
) -> dict:
    """
    Deploy VPC and ECS cluster stacks.
    
    Returns VPC params dict on success, None on failure.
    """
    vpc_template = template_dir / "cf_vpc.json"
    ecs_template = template_dir / "cf_ecs_cluster.json"
    
    # Verify templates exist
    for template in [vpc_template, ecs_template]:
        if not template.exists():
            print(f"❌ Template not found: {template}")
            return None
    
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
        return None
    
    # Deploy ECS Cluster (depends on VPC exports)
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=ECS_STACK_NAME,
        template_path=ecs_template,
        capabilities=["CAPABILITY_NAMED_IAM"],
    )
    
    if not success:
        print("\n❌ ECS Cluster deployment failed.")
        return None
    
    return vpc_params


def check_ecr_repo_exists(session: boto3.Session, repo_name: str) -> bool:
    """
    Check if an ECR repository exists outside of CloudFormation.
    
    This can happen when a stack is deleted but the ECR repo is retained
    (because it has images). CloudFormation will fail to create a new
    stack with the same repo name.
    
    Returns True if repo exists, False otherwise.
    """
    ecr_client = session.client("ecr")
    try:
        ecr_client.describe_repositories(repositoryNames=[repo_name])
        return True
    except ecr_client.exceptions.RepositoryNotFoundException:
        return False


def deploy_app(
    session: boto3.Session,
    cf_client,
    template_dir: Path,
    account_id: str,
    region: str,
    image_tag: str,
    use_alb: bool = True,
) -> bool:
    """
    Deploy the simple_dashboard app:
    1. Deploy app CloudFormation stack (creates ECR repo, task definition)
    2. Deploy ALB or service-only stack (creates ECS service)
    3. Build and push Docker image
    4. Start the service and wait for stabilization
    
    Args:
        use_alb: If True, deploys ALB stack. If False, deploys service-only stack.
    
    Returns True on success.
    """
    app_template = template_dir / "cf_app_definition.json"
    alb_template = template_dir / "cf_app_with_alb.json"
    no_alb_template = template_dir / "cf_app_no_alb.json"
    
    # Verify templates exist
    if not app_template.exists():
        print(f"❌ App template not found: {app_template}")
        return False
    
    service_stack_template = alb_template if use_alb else no_alb_template
    service_stack_name = ALB_STACK_NAME if use_alb else NO_ALB_STACK_NAME
    
    if not service_stack_template.exists():
        print(f"❌ Service template not found: {service_stack_template}")
        return False
    
    # Check if ECR repo exists outside CloudFormation (orphaned from deleted stack)
    # This would cause CloudFormation to fail when creating the app-definition stack
    try:
        stacks = cf_client.describe_stacks(StackName=APP_STACK_NAME)
        app_stack_exists = True
    except ClientError:
        app_stack_exists = False
    
    if not app_stack_exists and check_ecr_repo_exists(session, ECR_REPO_NAME):
        print(f"\n❌ ECR repository '{ECR_REPO_NAME}' already exists outside CloudFormation.")
        print(f"   This happens when a previous stack was deleted but the ECR repo was retained.")
        print(f"   To fix, delete the orphaned ECR repo:")
        print(f"   aws ecr delete-repository --repository-name {ECR_REPO_NAME} --force --region {region}")
        return False
    
    # Step 1: Deploy the app CloudFormation stack (creates ECR repo + task definition)
    print("\n📦 Step 1: Deploy app CloudFormation stack (ECR + Task Definition)...")
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=APP_STACK_NAME,
        template_path=app_template,
        parameters={"ImageTag": image_tag},
    )
    
    if not success:
        print("\n❌ App stack deployment failed.")
        return False
    
    # Step 2: Deploy ALB or service-only stack
    stack_type = "ALB + Service" if use_alb else "Service (no ALB)"
    print(f"\n📦 Step 2: Deploy {stack_type} stack...")
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=service_stack_name,
        template_path=service_stack_template,
    )
    
    if not success:
        print(f"\n❌ {stack_type} stack deployment failed.")
        return False
    
    # Step 3: Build and push Docker image
    print("\n📦 Step 3: Build and push Docker image...")
    image_uri = build_and_push_docker_image(
        session=session,
        account_id=account_id,
        region=region,
        app_path=SIMPLE_DASHBOARD_PATH,
        repo_name=ECR_REPO_NAME,
        image_tag=image_tag,
    )
    
    if not image_uri:
        print("\n❌ Docker build/push failed.")
        return False
    
    # Step 4: Force new deployment with desiredCount=1 (was 0 to avoid chicken-egg problem)
    print("\n📦 Step 4: Start service (desiredCount=1)...")
    ecs_client = session.client("ecs")
    
    try:
        ecs_client.update_service(
            cluster="devopshero-cluster",
            service="simple-dashboard",
            desiredCount=1,
            forceNewDeployment=True,
        )
        print("   ✅ Deployment triggered (desiredCount=1)")
    except ClientError as e:
        print(f"   ❌ Failed to trigger deployment: {e}")
        return False
    
    # Step 5: Wait for service to stabilize
    stable = wait_for_service_stable(
        ecs_client=ecs_client,
        cluster="devopshero-cluster",
        service="simple-dashboard",
        timeout_seconds=180,  # 3 minutes
    )
    
    if not stable:
        print("\n❌ Service failed to stabilize. Check ECS console for details.")
        return False
    
    return True


def check_stopped_tasks(ecs_client, cluster: str, service: str) -> list[str]:
    """
    Check if tasks are failing and return the reasons.
    
    Returns a list of failure reasons (empty if no failures).
    """
    reasons = []
    
    try:
        # Get recently stopped tasks
        stopped = ecs_client.list_tasks(
            cluster=cluster,
            serviceName=service,
            desiredStatus="STOPPED",
        )
        
        if not stopped["taskArns"]:
            return reasons
        
        # Get details on why they stopped (check up to 3 recent tasks)
        details = ecs_client.describe_tasks(
            cluster=cluster,
            tasks=stopped["taskArns"][:3],
        )
        
        for task in details["tasks"]:
            reason = task.get("stoppedReason", "Unknown")
            reasons.append(reason)
            
            # Check container-level failures
            for container in task.get("containers", []):
                if container.get("reason"):
                    reasons.append(f"Container '{container['name']}': {container['reason']}")
        
    except ClientError:
        pass
    
    return reasons


def wait_for_service_stable(
    ecs_client,
    cluster: str,
    service: str,
    timeout_seconds: int,
) -> bool:
    """
    Wait for ECS service to stabilize, with diagnostics on failure.
    
    Requires multiple consecutive stable checks to confirm the service isn't
    just briefly running before crashing.
    
    Returns True if service is stable, False if timed out or tasks failing.
    """
    import time
    
    print(f"\n📦 Step 5: Waiting for service to stabilize (timeout: {timeout_seconds}s)...")
    
    STABLE_CHECKS_REQUIRED = 3  # Need 3 consecutive stable checks (30 seconds)
    
    start = time.time()
    last_running = -1
    consecutive_failures = 0
    consecutive_stable = 0
    
    while time.time() - start < timeout_seconds:
        try:
            response = ecs_client.describe_services(cluster=cluster, services=[service])
            svc = response["services"][0]
            
            running = svc["runningCount"]
            desired = svc["desiredCount"]
            pending = svc["pendingCount"]
            
            if running != last_running:
                print(f"   Tasks: {running}/{desired} running, {pending} pending")
                last_running = running
                consecutive_stable = 0  # Reset stability counter on change
            
            if running == desired and desired > 0 and pending == 0:
                consecutive_stable += 1
                if consecutive_stable >= STABLE_CHECKS_REQUIRED:
                    print("   ✅ Service stable!")
                    return True
                elif consecutive_stable == 1:
                    print(f"   ⏳ Confirming stability ({STABLE_CHECKS_REQUIRED - consecutive_stable} more checks)...")
            else:
                consecutive_stable = 0
            
            # Check for task failures
            failure_reasons = check_stopped_tasks(ecs_client, cluster, service)
            if failure_reasons:
                consecutive_failures += 1
                consecutive_stable = 0  # Reset stability on failures
                if consecutive_failures >= 2:  # Show failures after 2 checks
                    print("   ⚠️  Tasks are failing:")
                    for reason in failure_reasons[:3]:  # Show up to 3 reasons
                        print(f"      ❌ {reason}")
                    
                    # If we've seen failures for 3+ consecutive checks, give up early
                    if consecutive_failures >= 4:
                        print("   ❌ Too many task failures, aborting")
                        return False
            else:
                consecutive_failures = 0
            
        except ClientError as e:
            print(f"   ⚠️  Error checking service: {e}")
        
        time.sleep(10)
    
    print(f"   ❌ Timed out after {timeout_seconds}s waiting for service")
    return False


def get_service_task_ip(session: boto3.Session) -> str | None:
    """
    Get the private IP of the running task for the simple-dashboard service.
    """
    ecs_client = session.client("ecs")
    
    try:
        # List tasks for the service
        tasks_response = ecs_client.list_tasks(
            cluster="devopshero-cluster",
            serviceName="simple-dashboard",
            desiredStatus="RUNNING",
        )
        
        if not tasks_response["taskArns"]:
            return None
        
        # Describe the first running task
        task_arn = tasks_response["taskArns"][0]
        task_details = ecs_client.describe_tasks(
            cluster="devopshero-cluster",
            tasks=[task_arn],
        )
        
        if not task_details["tasks"]:
            return None
        
        task = task_details["tasks"][0]
        
        # Get the ENI attachment
        for attachment in task.get("attachments", []):
            if attachment["type"] == "ElasticNetworkInterface":
                for detail in attachment["details"]:
                    if detail["name"] == "privateIPv4Address":
                        return detail["value"]
        
        return None
        
    except ClientError:
        return None


def get_alb_url(cf_client) -> str | None:
    """Get the ALB URL from the stack outputs."""
    try:
        response = cf_client.describe_stacks(StackName=ALB_STACK_NAME)
        stack = response["Stacks"][0]
        for output in stack.get("Outputs", []):
            if output["OutputKey"] == "AlbUrl":
                return output["OutputValue"]
    except ClientError:
        pass
    return None


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Deploy DevOpsHero infrastructure and apps")
    parser.add_argument("--infra-only", action="store_true", help="Deploy only VPC + ECS cluster")
    parser.add_argument("--app-only", action="store_true", help="Deploy only the app (assumes infra exists)")
    parser.add_argument("--no-alb", action="store_true", help="Deploy without ALB (access via VPN/bastion only)")
    parser.add_argument("--image-tag", default="latest", help="Docker image tag (default: latest)")
    args = parser.parse_args()
    
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
    
    vpc_params = None
    
    # Deploy infrastructure if not --app-only
    if not args.app_only:
        vpc_params = deploy_infrastructure(
            cf_client=cf_client,
            ec2_client=ec2_client,
            template_dir=template_dir,
        )
        
        if vpc_params is None:
            sys.exit(1)
    
    # Deploy app if not --infra-only
    if not args.infra_only:
        use_alb = not args.no_alb
        success = deploy_app(
            session=session,
            cf_client=cf_client,
            template_dir=template_dir,
            account_id=TARGET_ACCOUNT_ID,
            region=TARGET_REGION,
            image_tag=args.image_tag,
            use_alb=use_alb,
        )
        
        if not success:
            print("\n❌ App deployment failed.")
            sys.exit(1)
    
    # Print summary
    print("\n" + "=" * 60)
    print("🎉 Deployment complete!")
    print("=" * 60)
    print(f"\nAccount: {TARGET_ACCOUNT_ID}")
    print(f"Region:  {TARGET_REGION}")
    
    if vpc_params:
        print(f"\nVPC CIDR: {vpc_params['VpcCidr']}")
        print(f"  Public subnets:  {vpc_params['PublicSubnet1Cidr']}, {vpc_params['PublicSubnet2Cidr']}")
        print(f"  Private subnets: {vpc_params['PrivateSubnet1Cidr']}, {vpc_params['PrivateSubnet2Cidr']}")
    
    if not args.infra_only:
        print(f"\n📊 App: simple-dashboard")
        print(f"   Image tag: {args.image_tag}")
        
        if not args.no_alb:
            # Show ALB URL
            alb_url = get_alb_url(cf_client)
            if alb_url:
                print(f"\n🌐 App URL: {alb_url}")
                print(f"\n📝 Access the app at the URL above (public via ALB)")
            else:
                print("\n   ALB URL: (waiting for ALB to be ready...)")
        else:
            # Show task IP for VPN/bastion access
            task_ip = get_service_task_ip(session)
            if task_ip:
                print(f"   Task private IP: {task_ip}")
                print(f"   URL (from VPC): http://{task_ip}:8501")
            else:
                print("   Task IP: (waiting for task to start...)")
            
            print(f"\n📝 To access the app:")
            print(f"   1. Connect via VPN to the VPC")
            print(f"   2. Access http://<task-ip>:8501")
        
        print(f"\n   Or use ECS Exec to connect to the container:")
        print(f"   aws ecs execute-command --cluster devopshero-cluster \\")
        print(f"       --task <task-id> --container simple-dashboard \\")
        print(f"       --interactive --command /bin/sh")


if __name__ == "__main__":
    main()

