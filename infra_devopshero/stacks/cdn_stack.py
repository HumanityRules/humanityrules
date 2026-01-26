"""CDN Stack for DevOps Hero - CloudFront and Route53."""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from constructs import Construct


class CdnStack(Stack):
    """CloudFront distribution and Route53 records for DevOps Hero."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        certificate: acm.ICertificate,
        alb: elbv2.IApplicationLoadBalancer,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Look up the hosted zone
        hosted_zone = route53.HostedZone.from_lookup(self, "HostedZone", domain_name=domain_name)

        # CloudFront distribution with ALB origin
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            domain_names=[domain_name, f"*.{domain_name}"],
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.LoadBalancerV2Origin(
                    alb,
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                compress=True,
            ),
            # Cache static assets
            additional_behaviors={
                "/static/*": cloudfront.BehaviorOptions(
                    origin=origins.LoadBalancerV2Origin(
                        alb,
                        protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                    compress=True,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US, Canada, Europe only (cheapest)
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            enable_logging=False,  # Can enable later if needed
            comment="DevOps Hero Production CDN",
        )

        # Route53 A record for apex domain (devopshero.ai)
        route53.ARecord(
            self,
            "ApexARecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(self.distribution)),
        )

        # Route53 A record for wildcard (*.devopshero.ai)
        route53.ARecord(
            self,
            "WildcardARecord",
            zone=hosted_zone,
            record_name=f"*.{domain_name}",
            target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(self.distribution)),
        )

        CfnOutput(self, "DistributionId", value=self.distribution.distribution_id, export_name="doh-prod-distribution-id")
        CfnOutput(self, "DistributionDomainName", value=self.distribution.distribution_domain_name, export_name="doh-prod-distribution-domain")
        CfnOutput(self, "AppUrl", value=f"https://{domain_name}", export_name="doh-prod-app-url")
