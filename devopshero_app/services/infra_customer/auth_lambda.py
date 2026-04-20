"""
Auth Lambda construct — one per DOH environment.

Bundles lambdas/sidecar_auth/ with its dependencies, registers it as a Lambda
target on the env's shared ALB under a host-based rule for auth.<env-domain>,
and creates the Route53 alias record that points at the ALB.

See docs/sidecar_proxy_design.md and lambdas/sidecar_auth/README.md.
"""

from dataclasses import dataclass
from pathlib import Path

from aws_cdk import Aws, CfnOutput, DockerImage, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_targets as elbv2_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from constructs import Construct


# Reserved ALB listener rule priority for the auth Lambda. App rule priorities
# are hashed into 1000..41000 (_compute_listener_rule_priority), so any value
# under 1000 is safe. Picking 10 leaves slots 1..9 for future infra rules.
AUTH_LISTENER_RULE_PRIORITY = 10


# Path to lambdas/sidecar_auth/ relative to this file, used by both the Stack
# and the `ensure_auth_lambda_bundle_exists` helper that preps the deploy zip.
_REPO_ROOT = Path(__file__).resolve().parents[3]
AUTH_LAMBDA_SOURCE_DIR = _REPO_ROOT / "lambdas" / "sidecar_auth"


@dataclass
class AuthLambdaInputs:
    """Everything the AuthLambdaStack needs to wire itself up."""

    env_slug: str
    env_domain: str                # Parent domain, e.g. "ch-sandbox.chsandbox.com"
    shared_alb_https_listener_arn: str
    shared_alb_security_group_id: str
    shared_hosted_zone_id: str
    shared_hosted_zone_name: str
    sidecar_auth_config_secret_arn: str  # Secrets Manager ARN with {oidc_config: {...}, jwt_key: {...}}


class AuthLambdaStack(Stack):
    """Deploy the auth Lambda + its ALB listener rule + the auth.<env-domain> DNS record."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        inputs: AuthLambdaInputs,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{inputs.env_slug}-auth"
        auth_host = f"auth.{inputs.env_domain}"

        # IAM role: CloudWatch logs + read the per-env auth-config secret.
        lambda_role = iam.Role(
            self, "AuthLambdaRole",
            role_name=f"{prefix}-role"[:64],
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[inputs.sidecar_auth_config_secret_arn],
        ))

        log_group = logs.LogGroup(
            self, "AuthLambdaLogGroup",
            log_group_name=f"/aws/lambda/{prefix}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Bundle the Lambda code + dependencies. The bundling image and the
        # Lambda function must both be ARM64 so cryptography's native wheels
        # (_rust.abi3.so) get pip-resolved for the right arch; running the
        # bundler on an x86 image and the function on ARM (or vice-versa)
        # yields a cold-start ImportModuleError.
        # Use the SAM ARM64 build image explicitly so pip resolves ARM64
        # wheels — matches the Architecture.ARM_64 set on the function below.
        code = lambda_.Code.from_asset(
            str(AUTH_LAMBDA_SOURCE_DIR),
            bundling={
                "image": DockerImage.from_registry(
                    "public.ecr.aws/sam/build-python3.12:latest-arm64",
                ),
                "command": [
                    "bash", "-c",
                    (
                        "pip install --no-cache-dir -r requirements.txt -t /asset-output && "
                        "cp handler.py /asset-output/"
                    ),
                ],
            },
        )

        self.function = lambda_.Function(
            self, "AuthLambda",
            function_name=f"{prefix}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            # Run on Graviton so pip-installed native wheels (cryptography's Rust
            # bindings) match the architecture of the local bundling image that
            # produced them. Without this, x86_64 Lambda can't load the ARM64
            # .so the bundler pulled in and import handler crashes at cold start.
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=code,
            role=lambda_role,
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=log_group,
            environment={
                "DOH_ENV_DOMAIN": inputs.env_domain,
                "DOH_AUTH_BASE_URL": f"https://{auth_host}",
                "DOH_SIDECAR_AUTH_CONFIG_SECRET_ARN": inputs.sidecar_auth_config_secret_arn,
            },
        )

        # Wire the Lambda as a target under the existing HTTPS listener with a
        # host-header rule. One rule per env at a reserved priority.
        listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self, "ImportedHttpsListener",
            listener_arn=inputs.shared_alb_https_listener_arn,
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "ImportedAlbSg", inputs.shared_alb_security_group_id,
            ),
        )
        target_group = elbv2.ApplicationTargetGroup(
            self, "AuthTargetGroup",
            target_group_name=f"doh-{inputs.env_slug}-auth"[:32],
            targets=[elbv2_targets.LambdaTarget(self.function)],
        )
        elbv2.ApplicationListenerRule(
            self, "AuthListenerRule",
            listener=listener,
            priority=AUTH_LISTENER_RULE_PRIORITY,
            conditions=[elbv2.ListenerCondition.host_headers([auth_host])],
            action=elbv2.ListenerAction.forward([target_group]),
        )

        # Route53 alias record auth.<env-domain> -> shared ALB. We look up the
        # ALB via Fn.import_value on the hosted zone's ALB output names from
        # deploy_base.EcsClusterStack.
        from aws_cdk import Fn
        shared_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
            self, "ImportedSharedAlb",
            load_balancer_arn=Fn.import_value(f"devopshero-{inputs.env_slug}-shared-alb-arn"),
            security_group_id=inputs.shared_alb_security_group_id,
            load_balancer_dns_name=Fn.import_value(f"devopshero-{inputs.env_slug}-shared-alb-dns"),
            load_balancer_canonical_hosted_zone_id=Fn.import_value(
                f"devopshero-{inputs.env_slug}-shared-alb-canonical-hz-id",
            ),
        )
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "ImportedHostedZone",
            hosted_zone_id=inputs.shared_hosted_zone_id,
            zone_name=inputs.shared_hosted_zone_name,
        )
        route53.ARecord(
            self, "AuthAliasRecord",
            zone=hosted_zone,
            record_name=auth_host,
            target=route53.RecordTarget.from_alias(
                route53_targets.LoadBalancerTarget(shared_alb),
            ),
        )

        CfnOutput(self, "AuthLambdaArn", value=self.function.function_arn, export_name=f"{prefix}-lambda-arn")
        CfnOutput(self, "AuthHost", value=auth_host, export_name=f"{prefix}-host")
