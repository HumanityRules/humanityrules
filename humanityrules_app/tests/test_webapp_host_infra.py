"""CDK synth tests for environment wildcard DNS and webapp host routing."""

from aws_cdk import App
from aws_cdk.assertions import Template
from django.test import SimpleTestCase

from humanityrules_app.services.infra_customer import deploy_app, deploy_base
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig


HOSTED_ZONE = "humr.example.com"


def _render_app_stack(enable_webapp_hosts: bool) -> Template:
    cdk_app = App()
    stack = deploy_app.AppStack(
        scope=cdk_app,
        construct_id="TestAppStack",
        app_config=AppConfig(
            app_name="wolfie",
            cpu=512,
            memory=1024,
            alb_target_container="agent",
            containers=[
                ContainerConfig(
                    name="agent",
                    image_source="dockerfile",
                    ecr_repo_name="humr/staging/wolfie-agent",
                    container_port=8787,
                ),
            ],
            enable_webapp_hosts=enable_webapp_hosts,
        ),
        image_tag="test",
        env_slug="staging",
        resource_prefix="humr-staging-wolfie",
        subdomain="wolfie",
        database_connection_secret=None,
        shared_alb_hosted_zone=HOSTED_ZONE,
        env_bearer_shared_secrets_arn=None,
        auth_base_url=None,
    )
    return Template.from_stack(stack)


def _listener_rule_host_values(template: Template) -> list[list[str]]:
    rules = [
        resource
        for resource in template.to_json()["Resources"].values()
        if resource["Type"] == "AWS::ElasticLoadBalancingV2::ListenerRule"
    ]
    return [
        next(
            condition["HostHeaderConfig"]["Values"]
            for condition in rule["Properties"]["Conditions"]
            if condition["Field"] == "host-header"
        )
        for rule in rules
    ]


class AppStackWebappHostTests(SimpleTestCase):

    def test_webapp_hosts_widen_the_single_https_rule_without_app_dns_or_cert_attachment(self) -> None:
        template = _render_app_stack(enable_webapp_hosts=True)

        # One HTTPS host rule per agent; :80 redirects to HTTPS via listener default.
        self.assertEqual(
            _listener_rule_host_values(template=template),
            [[f"wolfie.{HOSTED_ZONE}", f"*-wolfie.{HOSTED_ZONE}"]],
        )
        template.resource_count_is("AWS::ElasticLoadBalancingV2::ListenerRule", 1)
        template.resource_count_is("AWS::Route53::RecordSet", 0)
        template.resource_count_is("AWS::ElasticLoadBalancingV2::ListenerCertificate", 0)

    def test_disabled_webapp_hosts_match_only_agent_host_without_app_dns(self) -> None:
        template = _render_app_stack(enable_webapp_hosts=False)

        self.assertEqual(_listener_rule_host_values(template=template), [[f"wolfie.{HOSTED_ZONE}"]])
        template.resource_count_is("AWS::ElasticLoadBalancingV2::ListenerRule", 1)
        template.resource_count_is("AWS::Route53::RecordSet", 0)


class ClusterStackWildcardDnsTests(SimpleTestCase):

    def test_hosted_zone_has_one_wildcard_alias_to_shared_alb(self) -> None:
        cdk_app = App()
        vpc_stack = deploy_base.VpcStack(
            scope=cdk_app,
            construct_id="TestVpcStack",
            env_slug="staging",
            vpc_cidr="10.90.0.0/16",
        )
        cluster_stack = deploy_base.EcsClusterStack(
            scope=cdk_app,
            construct_id="TestClusterStack",
            env_slug="staging",
            vpc=vpc_stack.vpc,
            shared_hosted_zone_name=HOSTED_ZONE,
            shared_hosted_zone_id="Z1234567890",
            existing_certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/test",
        )
        resources = Template.from_stack(cluster_stack).to_json()["Resources"]
        alb_logical_id = next(
            logical_id
            for logical_id, resource in resources.items()
            if resource["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        )
        records = [resource for resource in resources.values() if resource["Type"] == "AWS::Route53::RecordSet"]

        self.assertEqual(len(records), 1)
        properties = records[0]["Properties"]
        self.assertEqual(properties["Name"], f"*.{HOSTED_ZONE}.")
        self.assertEqual(properties["Type"], "A")
        self.assertEqual(
            properties["AliasTarget"]["DNSName"],
            {"Fn::Join": ["", ["dualstack.", {"Fn::GetAtt": [alb_logical_id, "DNSName"]}]]},
        )
        self.assertEqual(properties["AliasTarget"]["HostedZoneId"], {"Fn::GetAtt": [alb_logical_id, "CanonicalHostedZoneID"]})

    def test_http_listener_default_redirects_to_https_when_hosted_zone_configured(self) -> None:
        cdk_app = App()
        vpc_stack = deploy_base.VpcStack(
            scope=cdk_app,
            construct_id="TestVpcStack",
            env_slug="staging",
            vpc_cidr="10.90.0.0/16",
        )
        cluster_stack = deploy_base.EcsClusterStack(
            scope=cdk_app,
            construct_id="TestClusterStack",
            env_slug="staging",
            vpc=vpc_stack.vpc,
            shared_hosted_zone_name=HOSTED_ZONE,
            shared_hosted_zone_id="Z1234567890",
            existing_certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/test",
        )
        template = Template.from_stack(cluster_stack)

        template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {
                "Port": 80,
                "DefaultActions": [
                    {
                        "Type": "redirect",
                        "RedirectConfig": {"Protocol": "HTTPS", "Port": "443", "StatusCode": "HTTP_301"},
                    }
                ],
            },
        )
