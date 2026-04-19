"""
Mock PDP Lambda stack — used by the sidecar end-to-end test.

One per env (typically only stood up during testing). Same ALB-as-target pattern
as AuthLambdaStack: a small Python lambda behind a host-based listener rule on
the env's shared HTTPS listener at pdp-mock.<env-domain>. The sidecar's
DOH_PDP_URL is pointed at https://pdp-mock.<env-domain>/evaluate so the
decision pipeline stays local to the customer VPC.

NOT intended for production use — the real PDP is /api/pdp/evaluate on the
DOH control plane.
"""

from dataclasses import dataclass
from pathlib import Path

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_targets as elbv2_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from constructs import Construct


# Priority 11: sits right next to the auth Lambda rule (10) and well below the
# hashed app-rule space (1000..41000). Kept inside the reserved 1..99 infra band.
PDP_MOCK_LISTENER_RULE_PRIORITY = 11


_REPO_ROOT = Path(__file__).resolve().parents[3]
PDP_MOCK_SOURCE_DIR = _REPO_ROOT / "lambdas" / "pdp_mock"


@dataclass
class PdpMockLambdaInputs:
    env_slug: str
    env_domain: str                # Parent hosted zone, e.g. "chsandbox.com"
    shared_alb_https_listener_arn: str
    shared_alb_security_group_id: str
    shared_hosted_zone_id: str
    shared_hosted_zone_name: str
    allowed_usernames: list[str]   # usernames that should receive decision=allow


class PdpMockLambdaStack(Stack):
    """Deploy the mock PDP Lambda + its ALB rule + the pdp-mock.<env-domain> DNS record."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        inputs: PdpMockLambdaInputs,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{inputs.env_slug}-pdp-mock"
        mock_host = f"pdp-mock.{inputs.env_domain}"

        lambda_role = iam.Role(
            self, "PdpMockLambdaRole",
            role_name=f"{prefix}-role"[:64],
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )

        log_group = logs.LogGroup(
            self, "PdpMockLogGroup",
            log_group_name=f"/aws/lambda/{prefix}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Pure stdlib handler — no bundling step needed.
        self.function = lambda_.Function(
            self, "PdpMockLambda",
            function_name=prefix,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(PDP_MOCK_SOURCE_DIR)),
            role=lambda_role,
            timeout=Duration.seconds(5),
            memory_size=128,
            log_group=log_group,
            environment={
                "ALLOWED_USERNAMES": ",".join(inputs.allowed_usernames),
            },
        )

        listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self, "ImportedHttpsListener",
            listener_arn=inputs.shared_alb_https_listener_arn,
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "ImportedAlbSg", inputs.shared_alb_security_group_id,
            ),
        )
        target_group = elbv2.ApplicationTargetGroup(
            self, "PdpMockTargetGroup",
            target_group_name=f"doh-{inputs.env_slug}-pdp-mock"[:32],
            targets=[elbv2_targets.LambdaTarget(self.function)],
        )
        elbv2.ApplicationListenerRule(
            self, "PdpMockListenerRule",
            listener=listener,
            priority=PDP_MOCK_LISTENER_RULE_PRIORITY,
            conditions=[elbv2.ListenerCondition.host_headers([mock_host])],
            action=elbv2.ListenerAction.forward([target_group]),
        )

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
            self, "PdpMockAliasRecord",
            zone=hosted_zone,
            record_name=mock_host,
            target=route53.RecordTarget.from_alias(route53_targets.LoadBalancerTarget(shared_alb)),
        )

        CfnOutput(self, "PdpMockLambdaArn", value=self.function.function_arn, export_name=f"{prefix}-lambda-arn")
        CfnOutput(self, "PdpMockHost", value=mock_host, export_name=f"{prefix}-host")
