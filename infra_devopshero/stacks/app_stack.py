"""App Stack for DevOps Hero - ECS Service."""

from aws_cdk import Aws, CfnOutput, Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class AppStack(Stack):
    """ECS Fargate service for DevOps Hero Django app."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        cluster: ecs.ICluster,
        alb: elbv2.IApplicationLoadBalancer,
        https_listener: elbv2.IApplicationListener,
        alb_security_group: ec2.ISecurityGroup,
        task_execution_role: iam.IRole,
        log_group: logs.ILogGroup,
        ecr_repository: ecr.IRepository,
        database_secret: secretsmanager.ISecret,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Task Role (for the app container to access AWS services)
        task_role = iam.Role(
            self,
            "TaskRole",
            role_name="doh-prod-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Grant access to app secrets
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/*"],
        ))
        # Grant access to Bedrock for AI features
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))

        # Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            family="doh-prod-app",
            cpu=2048,
            memory_limit_mib=4096,
            execution_role=task_execution_role,
            task_role=task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # Container with secrets injection
        container = task_definition.add_container(
            "AppContainer",
            container_name="devopshero",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repository, tag="latest"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="devopshero", log_group=log_group),
            environment={
                "DJANGO_DEBUG": "0",
            },
            secrets={
                # Database credentials from Aurora-generated secret
                # App constructs DATABASE_URL from these components
                "DATABASE_HOST": ecs.Secret.from_secrets_manager(database_secret, field="host"),
                "DATABASE_PORT": ecs.Secret.from_secrets_manager(database_secret, field="port"),
                "DATABASE_NAME": ecs.Secret.from_secrets_manager(database_secret, field="dbname"),
                "DATABASE_USERNAME": ecs.Secret.from_secrets_manager(database_secret, field="username"),
                "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(database_secret, field="password"),
                # These secrets should be created manually in Secrets Manager before deployment
                "DJANGO_SECRET_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "DjangoSecret", "devopshero/prod/django"),
                    field="DJANGO_SECRET_KEY",
                ),
                "WORKOS_CLIENT_ID": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "WorkosSecret", "devopshero/prod/workos"),
                    field="WORKOS_CLIENT_ID",
                ),
                "WORKOS_API_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "WorkosSecret2", "devopshero/prod/workos"),
                    field="WORKOS_API_KEY",
                ),
                "GITHUB_APP_ID": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "GithubSecret", "devopshero/prod/github"),
                    field="GITHUB_APP_ID",
                ),
                "GITHUB_APP_CLIENT_ID": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "GithubSecret2", "devopshero/prod/github"),
                    field="GITHUB_APP_CLIENT_ID",
                ),
                "GITHUB_APP_CLIENT_SECRET": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "GithubSecret3", "devopshero/prod/github"),
                    field="GITHUB_APP_CLIENT_SECRET",
                ),
                "GITHUB_APP_PRIVATE_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "GithubSecret4", "devopshero/prod/github"),
                    field="GITHUB_APP_PRIVATE_KEY",
                ),
                "GITHUB_WEBHOOK_SECRET": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "GithubSecret5", "devopshero/prod/github"),
                    field="GITHUB_WEBHOOK_SECRET",
                ),
                "AWS_BEDROCK_REGION": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "BedrockSecret", "devopshero/prod/bedrock"),
                    field="AWS_BEDROCK_REGION",
                ),
                "AWS_BEDROCK_ACCESS_KEY_ID": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "BedrockSecret2", "devopshero/prod/bedrock"),
                    field="AWS_BEDROCK_ACCESS_KEY_ID",
                ),
                "AWS_BEDROCK_SECRET_ACCESS_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "BedrockSecret3", "devopshero/prod/bedrock"),
                    field="AWS_BEDROCK_SECRET_ACCESS_KEY",
                ),
                "DOH_API_SECRET_KEY": ecs.Secret.from_secrets_manager(
                    secretsmanager.Secret.from_secret_name_v2(self, "ApiSecret", "devopshero/prod/api"),
                    field="DOH_API_SECRET_KEY",
                ),
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health/ || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000, protocol=ecs.Protocol.TCP))

        # Security group for ECS tasks
        ecs_security_group = ec2.SecurityGroup(
            self,
            "EcsSecurityGroup",
            vpc=vpc,
            security_group_name="doh-prod-ecs-sg",
            description="Security group for ECS tasks",
            allow_all_outbound=True,
        )
        ecs_security_group.add_ingress_rule(
            peer=alb_security_group,
            connection=ec2.Port.tcp(8000),
            description="Allow traffic from ALB",
        )

        # Target Group
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup",
            target_group_name="doh-prod-app-tg",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                enabled=True,
                path="/health/",
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                healthy_http_codes="200",
            ),
        )

        # Add listener rule to route traffic to target group
        elbv2.ApplicationListenerRule(
            self,
            "ListenerRule",
            listener=https_listener,
            priority=100,
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            target_groups=[target_group],
        )

        # ECS Service
        self.service = ecs.FargateService(
            self,
            "EcsService",
            service_name="doh-prod-app",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=0,  # Start at 0, scaled up after image push
            assign_public_ip=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[ecs_security_group],
            enable_execute_command=True,
            min_healthy_percent=100,  # Zero-downtime: keep old task until new one is healthy
            max_healthy_percent=200,
        )
        self.service.attach_to_application_target_group(target_group)

        CfnOutput(self, "ServiceArn", value=self.service.service_arn, export_name="doh-prod-service-arn")
        CfnOutput(self, "ServiceName", value=self.service.service_name, export_name="doh-prod-service-name")
