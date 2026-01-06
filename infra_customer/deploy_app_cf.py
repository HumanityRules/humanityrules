#!/usr/bin/env python3
"""
Deploy DevOpsHero infrastructure and apps using CloudFormation templates.

This module provides deployment functions for CloudFormation-based deployments.
"""

import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

import cloudformation_utils
import ecr_utils
import ecs_service_stable
import route53_utils
import vpc_utils
from appconfig import AppConfig


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
    print(f"🚀 Deploying infrastructure (CloudFormation)")
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
    print(f"🚀 Deploying app: {app_config.app_name} (CloudFormation)")
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
        hosted_zone_id = route53_utils.get_hosted_zone_id(session=session, hosted_zone_name=app_config.hosted_zone_name)
        
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
    image_uri = ecr_utils.build_and_push_docker_image(
        session=session,
        account_id=account_id,
        region=region,
        app_name=app_config.app_name,
        ecr_repo_name=app_config.ecr_repo_name,
        app_source_path=app_config.app_source_path,
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


def deploy(
    session: boto3.Session,
    account_id: str,
    region: str,
    app_config: AppConfig,
    image_tag: str,
) -> bool:
    """
    Main deployment function for CloudFormation-based deployments.
    
    Args:
        session: Boto3 session with assumed role credentials
        account_id: Target AWS account ID
        region: Target AWS region
        app_config: Application configuration
        image_tag: Docker image tag to deploy
    
    Returns:
        True on success, False on failure
    """
    # Create AWS clients
    cf_client = session.client("cloudformation")
    ec2_client = session.client("ec2")
    
    # Get template paths
    template_dir = Path(__file__).parent
    
    # Deploy infrastructure
    success = deploy_infrastructure(
        cf_client=cf_client,
        ec2_client=ec2_client,
        template_dir=template_dir,
    )
    
    if not success:
        return False
    
    # Deploy app
    success = deploy_app(
        session=session,
        cf_client=cf_client,
        template_dir=template_dir,
        account_id=account_id,
        region=region,
        app_config=app_config,
        image_tag=image_tag,
    )
    
    if not success:
        print("\n❌ App deployment failed.")
        return False
    
    # Print summary
    print("\n" + "=" * 60)
    print("🎉 CloudFormation deployment complete!")
    print("=" * 60)
    print(f"\nAccount: {account_id}")
    print(f"Region:  {region}")
    
    print(f"\n📊 App: {app_config.app_name}")
    print(f"   Image tag: {image_tag}")
    
    # Show app URLs
    urls = cloudformation_utils.get_app_urls(
        cf_client,
        app_name=app_config.app_name,
        has_domain=bool(app_config.domain_name),
    )
    
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
    
    return True
