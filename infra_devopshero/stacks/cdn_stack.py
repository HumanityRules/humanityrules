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

        # ALB origin for the main app
        alb_origin = origins.LoadBalancerV2Origin(alb, protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY)

        # PostHog reverse proxy origins (bypasses ad blockers)
        # Using non-obvious path prefix as recommended by PostHog docs
        posthog_api_origin = origins.HttpOrigin("us.i.posthog.com", protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY)
        posthog_assets_origin = origins.HttpOrigin("us-assets.i.posthog.com", protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY)

        # PostHog cache policy: forward Authorization and Origin headers
        # Must use minimal TTL (not 0) to allow header forwarding - CloudFront requires it
        posthog_cache_policy = cloudfront.CachePolicy(
            self,
            "PosthogCachePolicy",
            cache_policy_name="doh-prod-posthog-cache",
            comment="Cache policy for PostHog proxy - forwards auth headers",
            header_behavior=cloudfront.CacheHeaderBehavior.allow_list("Authorization", "Origin"),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
            default_ttl=Duration.seconds(1),
            max_ttl=Duration.seconds(1),
            min_ttl=Duration.seconds(0),
        )

        # PostHog origin request policy: forward specific headers needed for PostHog
        # Can't use all() as it forwards Host header which breaks the proxy
        posthog_origin_request_policy = cloudfront.OriginRequestPolicy(
            self,
            "PosthogOriginRequestPolicy",
            origin_request_policy_name="doh-prod-posthog-origin-request",
            comment="Origin request policy for PostHog proxy",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list("Origin"),
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
            cookie_behavior=cloudfront.OriginRequestCookieBehavior.none(),
        )

        # CloudFront Function to strip /doh-ph prefix from URI
        posthog_rewrite_function = cloudfront.Function(
            self,
            "PosthogRewriteFunction",
            function_name="doh-prod-posthog-rewrite",
            comment="Strips /doh-ph prefix from PostHog proxy requests",
            code=cloudfront.FunctionCode.from_inline("""
function handler(event) {
    var request = event.request;
    // Strip /doh-ph prefix: /doh-ph/batch -> /batch
    request.uri = request.uri.replace(/^\\/doh-ph/, '');
    if (request.uri === '') request.uri = '/';
    return request;
}
"""),
        )

        # CloudFront Function to rewrite /doh-ph-static/* to /static/*
        posthog_static_rewrite_function = cloudfront.Function(
            self,
            "PosthogStaticRewriteFunction",
            function_name="doh-prod-posthog-static-rewrite",
            comment="Rewrites /doh-ph-static/* to /static/* for PostHog assets",
            code=cloudfront.FunctionCode.from_inline("""
function handler(event) {
    var request = event.request;
    // Rewrite /doh-ph-static/* to /static/*: /doh-ph-static/array.js -> /static/array.js
    request.uri = request.uri.replace(/^\\/doh-ph-static/, '/static');
    return request;
}
"""),
        )

        # CloudFront distribution with ALB origin
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            domain_names=[domain_name, f"*.{domain_name}"],
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=alb_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                compress=True,
            ),
            additional_behaviors={
                # Django static assets (CSS, JS, images)
                "/static/*": cloudfront.BehaviorOptions(
                    origin=alb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                    compress=True,
                ),
                # PostHog API proxy: /doh-ph/* -> us.i.posthog.com/*
                "/doh-ph/*": cloudfront.BehaviorOptions(
                    origin=posthog_api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                    cache_policy=posthog_cache_policy,
                    origin_request_policy=posthog_origin_request_policy,
                    response_headers_policy=cloudfront.ResponseHeadersPolicy.CORS_ALLOW_ALL_ORIGINS_WITH_PREFLIGHT_AND_SECURITY_HEADERS,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=posthog_rewrite_function,
                            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        ),
                    ],
                    compress=True,
                ),
                # PostHog static assets proxy: /doh-ph-static/* -> us-assets.i.posthog.com/static/*
                "/doh-ph-static/*": cloudfront.BehaviorOptions(
                    origin=posthog_assets_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    origin_request_policy=posthog_origin_request_policy,
                    response_headers_policy=cloudfront.ResponseHeadersPolicy.CORS_ALLOW_ALL_ORIGINS_WITH_PREFLIGHT_AND_SECURITY_HEADERS,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=posthog_static_rewrite_function,
                            event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        ),
                    ],
                    compress=True,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US, Canada, Europe only (cheapest)
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            enable_logging=False,  # Can enable later if needed
            comment="DevOps Hero Production CDN",
        )

        # Route53 A record for apex domain (humanityrules.io)
        route53.ARecord(
            self,
            "ApexARecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(self.distribution)),
        )

        # Route53 A record for wildcard (*.humanityrules.io)
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
