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

import cloudformation_utils
import ecs_service_stable
import iam_utils
import vpc_utils


# Configuration
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"



@dataclass
class AppConfig:
    """Configuration for deploying an app to ECS."""
    
    # Core identifiers
    app_name: str  # e.g., "simple-dashboard" - used in resource names
    ecr_repo_name: str  # e.g., "devopshero/simple-dashboard"
    
    # Container configuration
    container_port: int
    cpu: str
    memory: str
    
    # Health checks
    health_check_path: str  # For ALB health checks
    health_check_command: str | None  # For container health checks (CMD-SHELL)
    
    # Environment variables as list of {"name": str, "value": str}
    environment_variables: list[dict[str, str]]
    
    # Local paths
    app_source_path: Path | None  # Path to app source for Docker build
    
    # Domain configuration (for HTTPS and Route53)
    domain_name: str | None  # e.g., "simple-dashboard.chsandbox.com"
    hosted_zone_name: str | None  # e.g., "chsandbox.com" (must end with dot internally)
    
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
            "domain_name": self.domain_name,
            "hosted_zone_name": self.hosted_zone_name,
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
    print(f"{'='*60}")
    print(f"🐳 Building and pushing Docker image")
    print(f"   App: {app_config.app_name}")
    print(f"   Source: {app_config.app_source_path}")
    print(f"   Repository: {app_config.ecr_repo_name}")
    print(f"   Tag: {image_tag}")
    
    if not app_config.app_source_path:
        print(f"   ❌ No app source path configured")
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
) -> bool:
    """
    Deploy VPC and ECS cluster stacks.
    
    Returns True on success, False on failure.
    """
    print(f"\n\n{'='*60}")
    print(f"🚀 Deploying infrastructure")
    print(f"{'='*60}")
    
    vpc_stack_name = "devopshero-vpc"
    ecs_stack_name = "devopshero-ecs-cluster"
    
    vpc_template = template_dir / "cf_vpc.json"
    ecs_template = template_dir / "cf_ecs_cluster.json"
    
    # Check if VPC stack already exists (CIDRs are immutable, can't change them on update)
    vpc_stack_exists = cloudformation_utils.stack_exists(cf_client, vpc_stack_name)
    
    if vpc_stack_exists:
        # Stack exists - don't pass new CIDR params, CloudFormation will use existing values
        vpc_params = None
        print(f"\n📦 VPC stack '{vpc_stack_name}' already exists, updating if needed...")
    else:
        print(f"VPC stack '{vpc_stack_name}' does not exist, creating new stack...")
        # New stack - find available CIDR range
        vpc_params = vpc_utils.find_available_vpc_cidr(ec2_client)
    
    # Deploy VPC (independent of ECS cluster, but app stacks need both)
    success = cloudformation_utils.deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=vpc_stack_name,
        template_path=vpc_template,
        template_body=None,
        capabilities=None,
        parameters=vpc_params,
    )
    
    if not success:
        print("\n❌ VPC deployment failed. Stopping.")
        return False
    
    # Deploy ECS Cluster (independent of VPC, but app stacks need both)
    success = cloudformation_utils.deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=ecs_stack_name,
        template_path=ecs_template,
        template_body=None,
        capabilities=["CAPABILITY_NAMED_IAM"],
        parameters=None,
    )
    
    if not success:
        print("\n❌ ECS Cluster deployment failed.")
        return False
    
    return True



def get_hosted_zone_id(session: boto3.Session, hosted_zone_name: str) -> str | None:
    """
    Look up the Route53 hosted zone ID by name.
    
    Args:
        hosted_zone_name: The domain name (e.g., "chsandbox.com")
        
    Returns:
        The hosted zone ID if found, None otherwise.
    """
    route53_client = session.client("route53")
    
    # Ensure the name ends with a dot (Route53 convention)
    if not hosted_zone_name.endswith("."):
        hosted_zone_name = hosted_zone_name + "."
    
    try:
        response = route53_client.list_hosted_zones_by_name(
            DNSName=hosted_zone_name,
            MaxItems="1",
        )
        
        for zone in response.get("HostedZones", []):
            if zone["Name"] == hosted_zone_name:
                # Zone ID comes as "/hostedzone/XXXXX", extract just the ID
                zone_id = zone["Id"].replace("/hostedzone/", "")
                return zone_id
                
    except ClientError as e:
        print(f"   ❌ Failed to look up hosted zone: {e}")
    
    return None


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
    print(f"\n\n{'='*60}")
    print(f"🚀 Deploying app: {app_config.app_name}")
    print(f"{'='*60}")
    
    ecr_stack_name = f"devopshero-ecr-{app_config.app_name}"
    app_stack_name = f"devopshero-app-with-alb-{app_config.app_name}"

    print(f"   ECR stack: {ecr_stack_name}")
    print(f"   App stack: {app_stack_name}")
    
    # Jinja2 template paths
    ecr_template_j2 = template_dir / "cf_ecr.json"
    app_template_j2 = template_dir / "cf_app_with_alb.json"
    
    # Render templates with app config
    template_vars = app_config.to_template_vars()
    
    # Step 1: Deploy ECR repository stack
    print("\n📦 Step 1: Deploy ECR repository stack...")
    ecr_template_body = cloudformation_utils.render_jinja2_template(template_path=ecr_template_j2, variables=template_vars)
    
    success = cloudformation_utils.deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=ecr_stack_name,
        template_path=None,
        template_body=ecr_template_body,
        capabilities=None,
        parameters=None,
    )
    
    if not success:
        print("\n❌ ECR stack deployment failed.")
        return False
    
    # Step 2: Deploy App stack (Task Definition + ALB + Service)
    print("\n📦 Step 2: Deploy App stack (Task Definition + ALB + Service)...")
    app_template_body = cloudformation_utils.render_jinja2_template(template_path=app_template_j2, variables=template_vars)
    
    # Build parameters for the app stack
    app_stack_params = {"ImageTag": image_tag}
    
    # If domain is configured, look up the hosted zone ID
    if app_config.domain_name and app_config.hosted_zone_name:
        print(f"   🔍 Looking up hosted zone for {app_config.hosted_zone_name}...")
        hosted_zone_id = get_hosted_zone_id(session=session, hosted_zone_name=app_config.hosted_zone_name)
        
        if not hosted_zone_id:
            print(f"   ❌ Could not find hosted zone for {app_config.hosted_zone_name}")
            return False
        
        print(f"   ✅ Found hosted zone: {hosted_zone_id}")
        app_stack_params["HostedZoneId"] = hosted_zone_id
    
    success = cloudformation_utils.deploy_cloudformation_stack(
        cf_client=cf_client,
        stack_name=app_stack_name,
        template_path=None,
        template_body=app_template_body,
        capabilities=None,
        parameters=app_stack_params,
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
    stable = ecs_service_stable.wait_for_service_stable(
        ecs_client=ecs_client,
        cluster="devopshero-cluster",
        service=app_config.app_name,
        timeout_seconds=180,  # 3 minutes
    )
    
    if not stable:
        print("\n❌ Service failed to stabilize. Check ECS console for details.")
        return False
    
    return True



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


def get_app_urls(cf_client, app_config: AppConfig) -> dict[str, str | None]:
    """Get the app URLs from stack outputs."""
    stack_name = f"devopshero-app-with-alb-{app_config.app_name}"
    
    urls = {
        "alb_url": get_stack_output(cf_client, stack_name=stack_name, output_key="AlbUrl"),
    }
    
    if app_config.domain_name:
        urls["https_url"] = get_stack_output(cf_client, stack_name=stack_name, output_key="HttpsUrl")
    
    return urls



def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Deploy DevOpsHero infrastructure and apps")
    parser.add_argument("--infra-only", action="store_true", help="Deploy only VPC + ECS cluster")
    parser.add_argument("--app-only", action="store_true", help="Deploy only the app (assumes infra exists)")
    parser.add_argument("--image-tag", default="latest", help="Docker image tag (default: latest)")
    args = parser.parse_args()
        
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
        domain_name="simple-dashboard.chsandbox.com",
        hosted_zone_name="chsandbox.com",
    )
    # =========================================================================
        
    # Assume role into target account
    session = iam_utils.get_assumed_role_session(
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
    
    # Deploy infrastructure if not --app-only
    if not args.app_only:
        success = deploy_infrastructure(
            cf_client=cf_client,
            ec2_client=ec2_client,
            template_dir=template_dir,
        )
        
        if not success:
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
    
    if not args.infra_only:
        print(f"\n📊 App: {app_config.app_name}")
        print(f"   Image tag: {args.image_tag}")
        
        # Show app URLs
        urls = get_app_urls(cf_client, app_config=app_config)
        
        if urls.get("https_url"):
            print(f"\n🔒 App URL (HTTPS): {urls['https_url']}")
        
        if urls.get("alb_url"):
            print(f"🌐 App URL (ALB):   {urls['alb_url']}")
        else:
            print("\n   URLs: (waiting for ALB to be ready...)")
        
        print(f"\n   Or use ECS Exec to connect to the container:")
        print(f"   aws ecs execute-command --cluster devopshero-cluster \\")
        print(f"       --task <task-id> --container {app_config.app_name} \\")
        print(f"       --interactive --command /bin/sh")


if __name__ == "__main__":
    main()
