"""Redirect Stack for devopshero.co to devopshero.ai."""

from aws_cdk import Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct


class RedirectStack(Stack):
    """Redirect devopshero.co (apex and www) to devopshero.ai with 302 redirects."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        redirect_domain: str,
        target_domain: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Look up the hosted zone for the redirect domain
        hosted_zone = route53.HostedZone.from_lookup(self, "RedirectHostedZone", domain_name=redirect_domain)

        # Certificate for redirect domain (apex + www)
        certificate = acm.Certificate(
            self,
            "RedirectCertificate",
            domain_name=redirect_domain,
            subject_alternative_names=[f"www.{redirect_domain}"],
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # CloudFront Function to redirect all requests to target domain
        # Uses 302 (temporary) so browsers don't cache the redirect permanently
        redirect_function = cloudfront.Function(
            self,
            "RedirectFunction",
            function_name="doh-prod-co-redirect",
            comment=f"Redirects {redirect_domain} to {target_domain}",
            code=cloudfront.FunctionCode.from_inline(f"""
function handler(event) {{
    var request = event.request;
    var response = {{
        statusCode: 302,
        statusDescription: 'Found',
        headers: {{
            'location': {{ value: 'https://{target_domain}' + request.uri }}
        }}
    }};
    return response;
}}
"""),
        )

        # Dummy origin - CloudFront requires an origin even though we never reach it
        # The redirect function intercepts all requests before they hit the origin
        dummy_origin = origins.HttpOrigin(target_domain)

        # CloudFront distribution for redirect
        distribution = cloudfront.Distribution(
            self,
            "RedirectDistribution",
            domain_names=[redirect_domain, f"www.{redirect_domain}"],
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=dummy_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=redirect_function,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    ),
                ],
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            comment=f"Redirect {redirect_domain} to {target_domain}",
        )

        # Route53 A record for apex domain
        route53.ARecord(
            self,
            "RedirectApexARecord",
            zone=hosted_zone,
            record_name=redirect_domain,
            target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
        )

        # Route53 A record for www subdomain
        route53.ARecord(
            self,
            "RedirectWwwARecord",
            zone=hosted_zone,
            record_name=f"www.{redirect_domain}",
            target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
        )
