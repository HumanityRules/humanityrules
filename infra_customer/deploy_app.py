#!/usr/bin/env python3
"""
Deploy DevOpsHero infrastructure and apps to a customer account.

This script:
1. Assumes the cross-account role into the target AWS account
2. Deploys the VPC CloudFormation stack
3. Deploys the ECS Cluster CloudFormation stack
4. Builds and pushes Docker image to ECR
5. Deploys app to ECS Fargate with ALB

Usage:
    cd infra_customer
    uv run python deploy_app.py              # Deploy infra + app with ALB
    uv run python deploy_app.py --infra-only # Deploy only VPC + ECS cluster
    uv run python deploy_app.py --app-only   # Deploy only app (assumes infra exists)

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
from dotenv import load_dotenv
from dataclasses import dataclass, field

import boto3
from botocore.exceptions import ClientError

from deployment_utils import (
    AppConfig,
    deploy_cloudformation_stack,
    get_assumed_role_session,
    load_env,
    render_jinja2_template,
)
from vpc_utils import find_available_vpc_cidr
from ecs_service_stable import wait_for_service_stable


# Configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"

# Stack names (infrastructure - shared across all apps)
VPC_STACK_NAME = "devopshero-vpc"
ECS_STACK_NAME = "devopshero-ecs-cluster"



@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""
    
    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" - used in resource names
    ecr_repo_name: str  # e.g., "devopshero/simple-dashboard"
    
    # Container configuration
    container_port: int
    cpu: str = "256"  # Fargate CPU units
    memory: str = "512"  # Fargate memory in MB
    
    # Health checks
    health_check_path: str = "/"  # For ALB health checks
    health_check_command: str | None = None  # For container health checks (CMD-SHELL)
    
    # Environment variables as list of {"name": str, "value": str}
    environment_variables: list[dict[str, str]] = field(default_factory=list)
    
    # Local paths
    app_source_path: Path | None = None  # Path to app source for Docker build
    
    def to_template_vars(self) -> dict:
        """Convert to dict for Jinja2 template rendering."""
        return {
            "app_name": self.app_name,
            "ecr_repo_name": self.ecr_repo_name,
            "container_port": self.container_port,
            "cpu": self.cpu,
            "memory": self.memory,
            "health_check_path": self.health_check_path,
            "health_check_command": self.health_check_command,
            "environment_variables": self.environment_variables,
        }



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



def build_and_push_docker_image(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_config: AppConfig,
    image_tag: str,
) -> str:
    """
    Build Docker image and push to ECR.
    
    Returns the full image URI.
    """
    print(f"\n{'='*60}")
    print(f"🐳 Building and pushing Docker image")
    print(f"   App: {app_config.app_name}")
    print(f"   Source: {app_config.app_source_path}")
    print(f"   Repository: {app_config.ecr_repo_name}")
    print(f"   Tag: {image_tag}")
    
    if not app_config.app_source_path:
        print(f"   ❌ No app source path configured")
        return None
    
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
    image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{app_config.ecr_repo_name}:{image_tag}"
    
    # Build Docker image for AMD64 (Fargate runs on x86_64, not ARM)
    print(f"   ⏳ Building Docker image (platform: linux/amd64)...")
    build_result = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", image_uri, "."],
        cwd=app_config.app_source_path,
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
        template_body=None,
        capabilities=None,
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
        template_body=None,
        capabilities=["CAPABILITY_NAMED_IAM"],
        parameters=None,
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
    app_config: AppConfig,
    image_tag: str,
) -> bool:
    """
    Deploy an app:
    1. Render and deploy app CloudFormation stack (creates ECR repo, task definition)
    2. Render and deploy ALB stack (creates ALB and ECS service)
    3. Build and push Docker image
    4. Start the service and wait for stabilization
    
    Args:
        app_config: Configuration for the app to deploy
    
    Returns True on success.
    """
    app_stack_name = f"devopshero-app-{app_config.app_name}"
    alb_stack_name = f"devopshero-alb-{app_config.app_name}"
    
    # Jinja2 template paths
    app_template_j2 = template_dir / "cf_app_definition.json"
    alb_template_j2 = template_dir / "cf_app_with_alb.json"
    
    
    # Check if ECR repo exists outside CloudFormation (orphaned from deleted stack)
    try:
        cf_client.describe_stacks(StackName=app_stack_name)
        app_stack_exists = True
    except ClientError:
        app_stack_exists = False
    
    if not app_stack_exists and check_ecr_repo_exists(session, app_config.ecr_repo_name):
        print(f"\n❌ ECR repository '{app_config.ecr_repo_name}' already exists outside CloudFormation.")
        print(f"   This happens when a previous stack was deleted but the ECR repo was retained.")
        print(f"   To fix, delete the orphaned ECR repo:")
        print(f"   aws ecr delete-repository --repository-name {app_config.ecr_repo_name} --force --region {region}")
        return False
    
    # Render templates with app config
    template_vars = app_config.to_template_vars()
    
    # Step 1: Deploy the app CloudFormation stack (creates ECR repo + task definition)
    print("\n📦 Step 1: Deploy app CloudFormation stack (ECR + Task Definition)...")
    app_template_body = render_jinja2_template(app_template_j2, template_vars)
    
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=app_stack_name,
        template_path=None,
        template_body=app_template_body,
        capabilities=None,
        parameters={"ImageTag": image_tag},
    )
    
    if not success:
        print("\n❌ App stack deployment failed.")
        return False
    
    # Step 2: Deploy ALB stack
    print("\n📦 Step 2: Deploy ALB + Service stack...")
    alb_template_body = render_jinja2_template(alb_template_j2, template_vars)
    
    success = deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=alb_stack_name,
        template_path=None,
        template_body=alb_template_body,
        capabilities=None,
        parameters=None,
    )
    
    if not success:
        print("\n❌ ALB stack deployment failed.")
        return False
    
    # Step 3: Build and push Docker image
    print("\n📦 Step 3: Build and push Docker image...")
    image_uri = build_and_push_docker_image(
        session=session,
        account_id=account_id,
        region=region,
        app_config=app_config,
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
            service=app_config.app_name,
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
        service=app_config.app_name,
        timeout_seconds=180,  # 3 minutes
    )
    
    if not stable:
        print("\n❌ Service failed to stabilize. Check ECS console for details.")
        return False
    
    return True



def get_alb_url(cf_client, app_name: str) -> str | None:
    """Get the ALB URL from the stack outputs."""
    alb_stack_name = f"devopshero-alb-{app_name}"
    try:
        response = cf_client.describe_stacks(StackName=alb_stack_name)
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
    parser.add_argument("--image-tag", default="latest", help="Docker image tag (default: latest)")
    args = parser.parse_args()
    
    print("🚀 DevOpsHero Infrastructure Deployment")
    print("=" * 60)
    
    # Load environment
    load_env()
    
    # =========================================================================
    # APP CONFIGURATION
    # All app-specific settings are defined here in main() and passed down
    # =========================================================================
    app_config = AppConfig(
        app_name="simple-dashboard",
        ecr_repo_name="devopshero/simple-dashboard",
        container_port=8501,
        cpu="256",
        memory="512",
        health_check_path="/_stcore/health",
        health_check_command='python -c "import urllib.request; urllib.request.urlopen(\'http://localhost:8501/_stcore/health\', timeout=5)" || exit 1',
        environment_variables=[
            {"name": "STREAMLIT_SERVER_PORT", "value": "8501"},
            {"name": "STREAMLIT_SERVER_ADDRESS", "value": "0.0.0.0"},
            {"name": "STREAMLIT_SERVER_HEADLESS", "value": "true"},
            {"name": "STREAMLIT_BROWSER_GATHER_USAGE_STATS", "value": "false"},
        ],
        app_source_path=Path(__file__).parent.parent / "deployable_repos" / "simple_dashboard",
    )
    # =========================================================================
        
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
        success = deploy_app(
            session=session,
            cf_client=cf_client,
            template_dir=template_dir,
            account_id=TARGET_ACCOUNT_ID,
            region=TARGET_REGION,
            app_config=app_config,
            image_tag=args.image_tag,
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
        print(f"\n📊 App: {app_config.app_name}")
        print(f"   Image tag: {args.image_tag}")
        
        # Show ALB URL
        alb_url = get_alb_url(cf_client, app_config.app_name)
        if alb_url:
            print(f"\n🌐 App URL: {alb_url}")
        else:
            print("\n   ALB URL: (waiting for ALB to be ready...)")
        
        print(f"\n   Or use ECS Exec to connect to the container:")
        print(f"   aws ecs execute-command --cluster devopshero-cluster \\")
        print(f"       --task <task-id> --container {app_config.app_name} \\")
        print(f"       --interactive --command /bin/sh")


if __name__ == "__main__":
    main()
