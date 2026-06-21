"""ACM Certificate Stack for Humanity Rules."""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct


class CertStack(Stack):
    """ACM wildcard certificate for CloudFront. Must be deployed in us-east-1."""

    def __init__(self, scope: Construct, construct_id: str, domain_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Look up the existing hosted zone
        self.hosted_zone = route53.HostedZone.from_lookup(self, "HostedZone", domain_name=domain_name)

        # Create wildcard certificate with DNS validation
        # Covers both apex (humanityrules.io) and all subdomains (*.humanityrules.io)
        self.certificate = acm.Certificate(
            self,
            "WildcardCertificate",
            domain_name=domain_name,
            subject_alternative_names=[f"*.{domain_name}"],
            validation=acm.CertificateValidation.from_dns(self.hosted_zone),
        )

        CfnOutput(self, "CertificateArn", value=self.certificate.certificate_arn, export_name="humr-prod-cert-arn")
        CfnOutput(self, "HostedZoneId", value=self.hosted_zone.hosted_zone_id, export_name="humr-prod-hosted-zone-id")
