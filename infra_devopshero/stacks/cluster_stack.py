"""Cluster Stack for DevOps Hero - ECS Cluster and ALB."""

from aws_cdk import Aws, CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct


class ClusterStack(Stack):
    """ECS Fargate cluster with ALB for DevOps Hero."""

    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, ecr_repository: ecr.IRepository, certificate: acm.ICertificate, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ECS Cluster with Container Insights
        self.cluster = ecs.Cluster(
            self,
            "EcsCluster",
            cluster_name="doh-prod-cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENHANCED,
        )

        # Task Execution Role (for ECS to pull images and write logs)
        self.task_execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name="doh-prod-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
        )
        # Allow ECS to inject secrets as env vars
        self.task_execution_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:devopshero/*"],
        ))
        # Grant ECR pull permissions
        ecr_repository.grant_pull(self.task_execution_role)

        # CloudWatch Log Group for ECS tasks
        self.log_group = logs.LogGroup(
            self,
            "EcsLogGroup",
            log_group_name="/devopshero/prod/ecs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ALB Security Group - allows HTTPS from anywhere (CloudFront will connect here)
        self.alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            description="Security group for ALB - allows HTTPS from CloudFront",
            allow_all_outbound=True,
        )
        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from anywhere (CloudFront)",
        )

        # Internal ALB (CloudFront is the public entry point)
        # Using internet-facing so CloudFront can reach it, but only HTTP
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            load_balancer_name="doh-prod-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=self.alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # HTTPS Listener with default 404 action
        self.https_listener = self.alb.add_listener(
            "HttpsListener",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[certificate],
            default_action=elbv2.ListenerAction.fixed_response(
                status_code=404,
                content_type="text/plain",
                message_body="Not Found",
            ),
        )

        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn, export_name="doh-prod-cluster-arn")
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name, export_name="doh-prod-cluster-name")
        CfnOutput(self, "AlbArn", value=self.alb.load_balancer_arn, export_name="doh-prod-alb-arn")
        CfnOutput(self, "AlbDns", value=self.alb.load_balancer_dns_name, export_name="doh-prod-alb-dns")
        CfnOutput(self, "HttpsListenerArn", value=self.https_listener.listener_arn, export_name="doh-prod-https-listener-arn")
        CfnOutput(self, "TaskExecutionRoleArn", value=self.task_execution_role.role_arn, export_name="doh-prod-task-execution-role-arn")
        CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name, export_name="doh-prod-ecs-log-group")
