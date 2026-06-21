#!/usr/bin/env python3
"""DevOps Hero Production Infrastructure CDK App."""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aws_cdk import App, Environment

from stacks.cert_stack import CertStack
from stacks.vpc_stack import VpcStack
from stacks.storage_stack import StorageStack
from stacks.lambda_stack import LambdaStack
from stacks.cluster_stack import ClusterStack
from stacks.database_stack import DatabaseStack
from stacks.app_stack import AppStack
from stacks.cdn_stack import CdnStack

# Configuration
DOMAIN_NAME = "humanityrules.io"
AWS_ACCOUNT = os.environ.get("HUMR_AWS_ACCOUNT_ID", "555553041615")
AWS_REGION = "us-east-1"

# Environment for stacks that need explicit account/region (e.g., Route53 lookups)
env_us_east_1 = Environment(account=AWS_ACCOUNT, region=AWS_REGION)

app = App()

# Stack naming prefix
prefix = "humr-prod"

# 1. Certificate Stack (must be us-east-1 for CloudFront)
cert_stack = CertStack(
    app,
    f"{prefix}-cert",
    domain_name=DOMAIN_NAME,
    env=env_us_east_1,
)

# 2. VPC Stack
vpc_stack = VpcStack(
    app,
    f"{prefix}-vpc",
    env=env_us_east_1,
)

# 3. Storage Stack (S3 buckets, ECR, EFS)
storage_stack = StorageStack(
    app,
    f"{prefix}-storage",
    vpc=vpc_stack.vpc,
    env=env_us_east_1,
)
storage_stack.add_dependency(vpc_stack)

# 4. Lambda Stack (Install callback)
lambda_stack = LambdaStack(
    app,
    f"{prefix}-lambda",
    private_bucket=storage_stack.private_bucket,
    env=env_us_east_1,
)
lambda_stack.add_dependency(storage_stack)

# 5. Cluster Stack (ECS + ALB)
cluster_stack = ClusterStack(
    app,
    f"{prefix}-cluster",
    vpc=vpc_stack.vpc,
    ecr_repository=storage_stack.ecr_repository,
    certificate=cert_stack.certificate,
    env=env_us_east_1,
)
cluster_stack.add_dependency(cert_stack)
cluster_stack.add_dependency(vpc_stack)
cluster_stack.add_dependency(storage_stack)

# 6. Database Stack (Aurora Serverless v2)
database_stack = DatabaseStack(
    app,
    f"{prefix}-database",
    vpc=vpc_stack.vpc,
    env=env_us_east_1,
)
database_stack.add_dependency(vpc_stack)

# 7. App Stack (ECS Service)
app_stack = AppStack(
    app,
    f"{prefix}-app",
    vpc=vpc_stack.vpc,
    cluster=cluster_stack.cluster,
    alb=cluster_stack.alb,
    https_listener=cluster_stack.https_listener,
    alb_security_group=cluster_stack.alb_security_group,
    task_execution_role=cluster_stack.task_execution_role,
    log_group=cluster_stack.log_group,
    ecr_repository=storage_stack.ecr_repository,
    database_secret=database_stack.database_secret,
    claude_efs=storage_stack.claude_efs,
    claude_efs_access_point=storage_stack.claude_efs_access_point,
    env=env_us_east_1,
)
app_stack.add_dependency(storage_stack)
app_stack.add_dependency(cluster_stack)
app_stack.add_dependency(database_stack)

# 8. CDN Stack (CloudFront + Route53)
cdn_stack = CdnStack(
    app,
    f"{prefix}-cdn",
    domain_name=DOMAIN_NAME,
    certificate=cert_stack.certificate,
    alb=cluster_stack.alb,
    env=env_us_east_1,
)
cdn_stack.add_dependency(cert_stack)
cdn_stack.add_dependency(app_stack)

app.synth()
