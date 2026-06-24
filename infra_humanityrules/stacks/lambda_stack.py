"""Lambda Stack for Humanity Rules - Install callback Lambda."""

import os
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct


class LambdaStack(Stack):
    """Install callback Lambda that notifies the Humanity Rules backend when customers deploy."""

    def __init__(self, scope: Construct, construct_id: str, private_bucket: s3.IBucket, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Read the lambda code from the file
        lambda_code_path = Path(__file__).parent.parent / "install_callback_lambda.py"
        with open(lambda_code_path, "r") as f:
            lambda_code = f.read()

        # IAM role for Lambda
        lambda_role = iam.Role(
            self,
            "InstallCallbackLambdaRole",
            role_name="humr-prod-install-callback-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
        )

        # CloudWatch Log Group
        log_group = logs.LogGroup(
            self,
            "InstallCallbackLogGroup",
            log_group_name="/aws/lambda/humr-prod-install-callback",
            retention=logs.RetentionDays.TWO_YEARS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Home base for the shared install Lambda is always prod. Per-issuer routing happens at
        # call time: the customer stack passes the issuing control plane as ApiEndpoint, and the
        # Lambda honours it only when allow-listed (otherwise it falls back here). See
        # resolve_api_endpoint in install_callback_lambda.py.
        api_endpoint = "https://humanityrules.io"
        api_secret_key = os.environ.get("HUMR_API_SECRET_KEY", "")
        allowed_api_endpoints = os.environ.get(
            "HUMR_API_ALLOWED_ENDPOINTS", "https://humanityrules.io,https://humanityrules.ngrok.io"
        )

        # Lambda function with inline code
        self.lambda_function = lambda_.Function(
            self,
            "InstallCallbackLambda",
            function_name="humr-prod-install-callback",
            description="Handles CloudFormation Custom Resource callbacks when customers connect their AWS accounts",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(lambda_code),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "HUMR_API_ENDPOINT": api_endpoint,
                "HUMR_API_SECRET_KEY": api_secret_key,
                "HUMR_API_ALLOWED_ENDPOINTS": allowed_api_endpoints,
            },
            log_group=log_group,
        )

        # Allow any principal to invoke (for CloudFormation custom resources)
        self.lambda_function.add_permission(
            "AllowCloudFormationInvoke",
            principal=iam.AnyPrincipal(),
            action="lambda:InvokeFunction",
        )

        CfnOutput(self, "LambdaArn", value=self.lambda_function.function_arn, export_name="humr-prod-install-callback-lambda-arn")
        CfnOutput(self, "LambdaName", value=self.lambda_function.function_name, export_name="humr-prod-install-callback-lambda-name")
