"""Sandbox Stack for Humanity Rules.

Creates the IAM role the control plane assumes to deploy into HumR's own account when
that account is offered to every org as the shared "Humanity Rules Sandbox". This mirrors
the role customers create via cf_install_template.json, but here we create it ourselves in
our own account. Its name is humr-{external_id} so the normal assume-role path
(arn:aws:iam::{account}:role/humr-{external_id}) resolves to it unchanged.
"""

from dataclasses import dataclass

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct


@dataclass(frozen=True)
class SandboxAwsAccountCfg:
    """Config for the shared sandbox account.

    `external_id` is used at synth to name the assume-role (SandboxStack); the running
    container receives it via Secrets Manager (humr/prod/sandbox), never as a plain env var.
    """

    external_id: str
    account_id: str
    region: str
    hosted_zone: str

    @property
    def enabled(self) -> bool:
        """True only when both the external id and account id are set."""
        return bool(self.external_id and self.account_id)


class SandboxStack(Stack):
    """IAM role the CP assumes to deploy into the shared Humanity Rules sandbox account."""

    def __init__(self, scope: Construct, construct_id: str, cp_account_id: str, external_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self,
            "SandboxRole",
            role_name=f"humr-{external_id}",
            assumed_by=iam.AccountPrincipal(cp_account_id).with_conditions({
                "StringEquals": {"sts:ExternalId": external_id},
            }),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")],
        )

        CfnOutput(self, "SandboxRoleArn", value=role.role_arn, export_name="humr-prod-sandbox-role-arn")
