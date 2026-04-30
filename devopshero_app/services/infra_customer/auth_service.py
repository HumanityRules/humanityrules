"""
Auth service construct — one Fargate service per DOH environment.

Runs the policy-proxy image with DOH_ROLE=auth. Fronted by the env's shared
ALB via a host-based listener rule on auth.<env-domain>; creates the Route53
alias record that points at the ALB.

Replaced the previous Lambda-based design (see git history for auth_lambda.py):
the Lambda path required Docker-based bundling that Fargate control planes
can't run, forcing a prebuild step in the control-plane image. Running the
auth service as an ECS task reuses the policy-proxy image that the env already
has in its ECR repo and eliminates the packaging gymnastics.

See docs/policy_proxy_design.md and template_repos/policy_proxy/policy_proxy/auth.py.
"""

from dataclasses import dataclass

from aws_cdk import Aws, CfnOutput, Duration, Fn, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from constructs import Construct

from . import deploy_base


# Reserved ALB listener rule priority for the auth service. App rule priorities
# are hashed into 1000..41000 (_compute_listener_rule_priority), so any value
# under 1000 is safe. Picking 10 leaves slots 1..9 for future infra rules.
# Preserved from the previous Lambda-based stack for cutover compatibility.
AUTH_LISTENER_RULE_PRIORITY = 10

# The auth container listens on this port. Matches the policy-proxy image
# default; kept small and fixed because the port lives only inside the task's
# ENI and its ALB target group.
AUTH_CONTAINER_PORT = 8443


@dataclass
class AuthServiceInputs:
    """Everything the AuthServiceStack needs to wire itself up."""

    env_slug: str
    env_domain: str                           # Parent domain, e.g. "ch-sandbox.chsandbox.com"
    policy_proxy_image_uri: str               # Full ECR image URI for the policy-proxy image (auth role)
    policy_proxy_auth_config_secret_arn: str  # Secrets Manager ARN with {oidc_config, jwt_key}
    shared_alb_https_listener_arn: str
    shared_alb_security_group_id: str
    shared_hosted_zone_id: str
    shared_hosted_zone_name: str


class AuthServiceStack(Stack):
    """Deploy the auth Fargate service + ALB listener rule + auth.<env-domain> DNS record."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        inputs: AuthServiceInputs,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{inputs.env_slug}-auth"
        auth_host = f"auth.{inputs.env_domain}"

        # Reuse the env's VPC, cluster, log group, etc. Pass the hosted zone so
        # the helper imports the HTTPS listener ARN (we need it for the rule).
        env_infra = deploy_base.import_environment_infrastructure(
            scope=self, env_slug=inputs.env_slug, shared_alb_hosted_zone=inputs.shared_hosted_zone_name,
        )

        # Task role: read the per-env auth-config secret (OIDC creds + JWT key).
        # No other AWS access needed — Okta calls are outbound HTTPS, no IAM.
        task_role = iam.Role(
            self, "AuthTaskRole",
            role_name=f"{prefix}-task-role"[:64],
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[inputs.policy_proxy_auth_config_secret_arn],
        ))

        task_definition = ecs.FargateTaskDefinition(
            self, "AuthTaskDefinition",
            family=prefix[:255],
            cpu=256,
            memory_limit_mib=512,
            execution_role=env_infra.task_execution_role,
            task_role=task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        container = task_definition.add_container(
            "AuthContainer",
            container_name=f"{prefix}",
            image=ecs.ContainerImage.from_registry(inputs.policy_proxy_image_uri),
            essential=True,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=prefix,
                log_group=env_infra.log_group,
            ),
            environment={
                "DOH_ROLE": "auth",
                "DOH_ENV_DOMAIN": inputs.env_domain,
                "DOH_AUTH_BASE_URL": f"https://{auth_host}",
                "DOH_LISTEN_PORT": str(AUTH_CONTAINER_PORT),
                "DOH_POLICY_PROXY_AUTH_CONFIG_SECRET_ARN": inputs.policy_proxy_auth_config_secret_arn,
            },
        )
        container.add_port_mappings(ecs.PortMapping(container_port=AUTH_CONTAINER_PORT, protocol=ecs.Protocol.TCP))

        # One task is enough: logins are bursty but each request is short, and
        # a Fargate task handles many concurrent Okta exchanges. Rolling deploys
        # bring a second task up before draining the first (max=200, min=100).
        service = ecs.FargateService(
            self, "AuthService",
            service_name=prefix[:255],
            cluster=env_infra.cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[env_infra.default_security_group],
            enable_execute_command=True,
            min_healthy_percent=100,
            max_healthy_percent=200,
            health_check_grace_period=Duration.seconds(60),
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )

        target_group = elbv2.ApplicationTargetGroup(
            self, "AuthTargetGroup",
            target_group_name=f"doh-{inputs.env_slug}-auth"[:32].rstrip("-"),
            vpc=env_infra.vpc,
            port=AUTH_CONTAINER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                enabled=True,
                path="/__policy_proxy/healthz",
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(10),
                timeout=Duration.seconds(2),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                healthy_http_codes="200",
            ),
        )
        target_group.add_target(service.load_balancer_target(
            container_name=f"{prefix}", container_port=AUTH_CONTAINER_PORT,
        ))

        listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self, "ImportedHttpsListener",
            listener_arn=inputs.shared_alb_https_listener_arn,
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "ImportedAlbSg", inputs.shared_alb_security_group_id,
            ),
        )
        elbv2.ApplicationListenerRule(
            self, "AuthListenerRule",
            listener=listener,
            priority=AUTH_LISTENER_RULE_PRIORITY,
            conditions=[elbv2.ListenerCondition.host_headers([auth_host])],
            action=elbv2.ListenerAction.forward([target_group]),
        )

        # Route53 alias record auth.<env-domain> -> shared ALB.
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
            target=route53.RecordTarget.from_alias(route53_targets.LoadBalancerTarget(shared_alb)),
        )

        CfnOutput(self, "AuthServiceArn", value=service.service_arn, export_name=f"{prefix}-service-arn")
        CfnOutput(self, "AuthHost", value=auth_host, export_name=f"{prefix}-host")
