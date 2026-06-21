"""VPC Stack for DevOps Hero."""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class VpcStack(Stack):
    """VPC with public and private subnets for DevOps Hero production."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC with public and private subnets across 2 AZs
        # Single NAT Gateway for cost optimization (acceptable for early stage)
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name="humr-prod-vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
            ],
        )

        # Default security group for internal VPC traffic
        self.default_security_group = ec2.SecurityGroup(
            self,
            "DefaultSecurityGroup",
            vpc=self.vpc,
            security_group_name="humr-prod-default-sg",
            description="Default security group - allows VPC inbound and all outbound",
            allow_all_outbound=True,
        )
        self.default_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/16"),
            connection=ec2.Port.all_traffic(),
            description="Allow all traffic from within VPC",
        )

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name="humr-prod-vpc-id")
        CfnOutput(self, "VpcCidr", value=self.vpc.vpc_cidr_block, export_name="humr-prod-vpc-cidr")
        CfnOutput(self, "DefaultSecurityGroupId", value=self.default_security_group.security_group_id, export_name="humr-prod-default-sg-id")
