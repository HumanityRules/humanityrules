"""
End-to-end policy-proxy test.

Deploys a real Hermes Personal Assistant into the target environment — with
its policy-proxy container, the auth Lambda, per-env secrets, and a mock PDP —
then verifies the full request flow by minting doh_session cookies directly
(rather than going through Okta) and curling the deployed app.

What each phase does, at a glance:
  0  preflight        — resolve models + validate prerequisites
  1  seed-abac        — install the PA owner ABAC policy on the org
  2  create-env       — create + provision the target env if missing
  3  pdp-mock         — deploy the mock PDP behind pdp-mock.<env-domain>
  4  deploy           — deploy_from_template(hermes-personal) + run deployment inline
  5  smoke            — JWKS + policy-proxy health + unauthenticated redirect
  6  mint             — fetch the env's private key, sign owner + non-owner cookies
  7  verify           — curl the deployed app with each cookie, assert decisions
  8  cleanup          — tear down the PA deployment (leaves env-wide infra)

Example:
  uv run manage.py policy_proxy_e2e_test \\
      --aws-account "Humanity Rules Sandbox" \\
      --org humr \\
      --env-slug policy-proxy-e2e \\
      --hosted-zone chsandbox.com \\
      --owner vmendi@gmail.com \\
      --non-owner robert.thompson \\
      --yes

Rerun a failed test from a specific phase:
  uv run manage.py policy_proxy_e2e_test ... --skip-until-phase verify
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from asgiref.sync import async_to_sync
from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_targets as elbv2_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from constructs import Construct
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app import models
from devopshero_app.services import abac
from devopshero_app.services.app_templates import template_deploy_service
from devopshero_app.services.infra_customer import (
    cdk_utils,
    iam_utils,
    route53_utils,
)
from devopshero_app.services.jobs import (
    app_deployment_executor,
    environment_provisioning_executor,
)

logger = logging.getLogger(__name__)


PHASES = ["preflight", "seed-abac", "create-env", "pdp-mock", "deploy", "smoke", "mint", "verify", "cleanup"]

# Mock PDP Lambda stack (pdp-mock.<env-domain>): ALB rule + Lambda, test-only — not the control-plane PDP.
# Priority 11: next to auth Lambda (10), below app rules (1000+). Reserved 1..99 infra band.
PDP_MOCK_LISTENER_RULE_PRIORITY = 11
_REPO_ROOT = Path(__file__).resolve().parents[3]
PDP_MOCK_SOURCE_DIR = _REPO_ROOT / "lambdas" / "pdp_mock"


@dataclass
class PdpMockLambdaInputs:
    env_slug: str
    env_domain: str                # Parent hosted zone, e.g. "chsandbox.com"
    shared_alb_https_listener_arn: str
    shared_alb_security_group_id: str
    shared_hosted_zone_id: str
    shared_hosted_zone_name: str
    allowed_usernames: list[str]   # usernames that should receive decision=allow


class PdpMockLambdaStack(Stack):
    """Deploy the mock PDP Lambda + its ALB rule + the pdp-mock.<env-domain> DNS record."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        inputs: PdpMockLambdaInputs,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"devopshero-{inputs.env_slug}-pdp-mock"
        mock_host = f"pdp-mock.{inputs.env_domain}"

        lambda_role = iam.Role(
            self, "PdpMockLambdaRole",
            role_name=f"{prefix}-role"[:64],
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )

        log_group = logs.LogGroup(
            self, "PdpMockLogGroup",
            log_group_name=f"/aws/lambda/{prefix}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Pure stdlib handler — no bundling step needed.
        self.function = lambda_.Function(
            self, "PdpMockLambda",
            function_name=prefix,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(PDP_MOCK_SOURCE_DIR)),
            role=lambda_role,
            timeout=Duration.seconds(5),
            memory_size=128,
            log_group=log_group,
            environment={
                "ALLOWED_USERNAMES": ",".join(inputs.allowed_usernames),
            },
        )

        listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self, "ImportedHttpsListener",
            listener_arn=inputs.shared_alb_https_listener_arn,
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "ImportedAlbSg", inputs.shared_alb_security_group_id,
            ),
        )
        target_group = elbv2.ApplicationTargetGroup(
            self, "PdpMockTargetGroup",
            target_group_name=f"doh-{inputs.env_slug}-pdp-mock"[:32],
            targets=[elbv2_targets.LambdaTarget(self.function)],
        )
        elbv2.ApplicationListenerRule(
            self, "PdpMockListenerRule",
            listener=listener,
            priority=PDP_MOCK_LISTENER_RULE_PRIORITY,
            conditions=[elbv2.ListenerCondition.host_headers([mock_host])],
            action=elbv2.ListenerAction.forward([target_group]),
        )

        shared_alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
            self, "ImportedSharedAlb",
            load_balancer_arn=Fn.import_value(f"devopshero-{inputs.env_slug}-shared-alb-arn"),
            security_group_id=inputs.shared_alb_security_group_id,
            load_balancer_dns_name=Fn.import_value(f"devopshero-{inputs.env_slug}-shared-alb-dns"),
            load_balancer_canonical_hosted_zone_id=Fn.import_value(
                f"devopshero-{inputs.env_slug}-shared-alb-canonical-hz-id",
            ),
        )
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "ImportedHostedZone",
            hosted_zone_id=inputs.shared_hosted_zone_id,
            zone_name=inputs.shared_hosted_zone_name,
        )
        route53.ARecord(
            self, "PdpMockAliasRecord",
            zone=hosted_zone,
            record_name=mock_host,
            target=route53.RecordTarget.from_alias(route53_targets.LoadBalancerTarget(shared_alb)),
        )

        CfnOutput(self, "PdpMockLambdaArn", value=self.function.function_arn, export_name=f"{prefix}-lambda-arn")
        CfnOutput(self, "PdpMockHost", value=mock_host, export_name=f"{prefix}-host")


@dataclass
class TestContext:
    aws_account: models.AWSAccount
    organization: models.Organization
    env_slug: str                           # what --env-slug the operator passed
    env: models.Environment | None          # populated once create-env runs / confirms
    owner_user: models.User
    non_owner_user: models.User
    hosted_zone: str
    region: str
    app_slug: str
    app_hostname: str
    pdp_mock_url: str
    app: models.App | None = None


class Command(BaseCommand):
    help = "End-to-end policy-proxy test against a real AWS environment."

    def add_arguments(self, parser):
        parser.add_argument("--aws-account", required=True, help="AWS account name (e.g., 'Humanity Rules Sandbox').")
        parser.add_argument("--org", required=True, help="Organization slug (e.g., 'humr').")
        parser.add_argument("--env-slug", required=True, help="Environment slug to use/create.")
        parser.add_argument("--hosted-zone", required=True, help="Hosted zone for HTTPS (e.g., chsandbox.com).")
        parser.add_argument("--region", default="us-east-1", help="AWS region (default us-east-1).")
        parser.add_argument("--owner", required=True, help="Username of the PA owner (allow path).")
        parser.add_argument("--non-owner", required=True, help="Username of the deny-path user.")
        parser.add_argument("--app-slug", default="policy-proxy-e2e-pa", help="App slug for the test PA.")
        parser.add_argument("--yes", action="store_true", help="Skip the 'proceed?' prompt.")
        parser.add_argument(
            "--skip-until-phase", choices=PHASES, default=None,
            help=f"Skip phases up to (not including) this one. Choices: {PHASES}",
        )
        parser.add_argument("--skip-cleanup", action="store_true", help="Leave the deployed PA in place.")

    def handle(self, *args, **opts):
        skip_until = opts["skip_until_phase"]
        start_index = PHASES.index(skip_until) if skip_until else 0
        phases_to_run = PHASES[start_index:]

        ctx = self._phase_preflight(opts, force_run="preflight" in phases_to_run)

        if not opts["yes"] and "preflight" in phases_to_run:
            self.stdout.write(self.style.WARNING("\nThis will create real AWS resources in the target account."))
            answer = input("Proceed? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                self.stdout.write("Aborted.")
                return

        if "seed-abac" in phases_to_run:
            self._phase_seed_abac(ctx)
        if "create-env" in phases_to_run:
            self._phase_create_env(ctx)
        if "pdp-mock" in phases_to_run:
            self._phase_deploy_pdp_mock(ctx)
        if "deploy" in phases_to_run:
            self._phase_deploy_pa(ctx)
        else:
            # The later phases need the App row — hydrate it if we skipped deploy.
            ctx.app = models.App.objects.filter(
                organization=ctx.organization, slug=ctx.app_slug,
            ).first()
        if "smoke" in phases_to_run:
            self._phase_smoke(ctx)
        cookies = None
        if "mint" in phases_to_run:
            cookies = self._phase_mint(ctx)
        if "verify" in phases_to_run:
            if cookies is None:
                cookies = self._phase_mint(ctx)
            self._phase_verify(ctx, cookies)
        if "cleanup" in phases_to_run and not opts["skip_cleanup"]:
            self._phase_cleanup(ctx)

        self.stdout.write(self.style.SUCCESS("\nAll requested phases completed."))

    # ------------------------------------------------------------------
    # Phase 0 — preflight
    # ------------------------------------------------------------------

    def _phase_preflight(self, opts: dict, force_run: bool) -> TestContext:
        self._banner("Phase 0: preflight")

        try:
            aws_account = models.AWSAccount.objects.get(name=opts["aws_account"])
        except models.AWSAccount.DoesNotExist:
            raise CommandError(f"AWS account {opts['aws_account']!r} not found.")

        try:
            organization = models.Organization.objects.get(slug=opts["org"])
        except models.Organization.DoesNotExist:
            raise CommandError(f"Organization {opts['org']!r} not found.")

        if aws_account.organization_id != organization.pk:
            raise CommandError(
                f"AWS account {aws_account.name!r} belongs to org "
                f"{aws_account.organization.slug!r}, not {organization.slug!r}.",
            )

        try:
            template = models.AppTemplate.objects.get(slug="hermes-personal")
        except models.AppTemplate.DoesNotExist:
            raise CommandError(
                "hermes-personal template not found. Run 'uv run manage.py seed_app_templates' first.",
            )
        if not any(c.get("image_source") == "policy_proxy" for c in (template.containers or [])):
            raise CommandError(
                "hermes-personal template has no image_source=policy_proxy container. "
                "Re-run 'uv run manage.py seed_app_templates' to pick up the new fields.",
            )

        owner_user = self._resolve_user(organization=organization, identifier=opts["owner"], role="owner")
        non_owner_user = self._resolve_user(organization=organization, identifier=opts["non_owner"], role="non-owner")
        if not owner_user.oidc_sub:
            raise CommandError(
                f"Owner user {owner_user.username!r} has no oidc_sub; set one first "
                f"(see docs/okta_oidc_setup.md).",
            )
        if not non_owner_user.oidc_sub:
            # We mint a synthetic sub for the JWT only — no real Okta needed.
            non_owner_user.oidc_sub = f"synthetic|{non_owner_user.username}"
            non_owner_user.save(update_fields=["oidc_sub"])
            self.stdout.write(
                self.style.WARNING(
                    f"Set synthetic oidc_sub on non-owner user {non_owner_user.username!r}.",
                )
            )

        if not (organization.oidc_issuer_url and organization.oidc_client_id and organization.oidc_client_secret):
            raise CommandError(
                f"Organization {organization.slug!r} has no OIDC config. "
                f"The auth Lambda needs it even though the test bypasses real Okta. "
                f"Run 'uv run manage.py setup_oidc_org --slug {organization.slug} ...' first.",
            )

        if not (settings.DOH_AWS_ACCESS_KEY and settings.DOH_AWS_SECRET_KEY):
            raise CommandError(
                "DOH_AWS_ACCESS_KEY / DOH_AWS_SECRET_KEY are not set; can't assume target role.",
            )

        app_hostname = f"{opts['app_slug']}.{opts['hosted_zone']}"
        pdp_mock_url = f"https://pdp-mock.{opts['hosted_zone']}/evaluate"

        self.stdout.write(f"  aws_account  : {aws_account.name} ({aws_account.aws_account_id})")
        self.stdout.write(f"  organization : {organization.slug}")
        self.stdout.write(f"  env slug     : {opts['env_slug']}")
        self.stdout.write(f"  hosted zone  : {opts['hosted_zone']}")
        self.stdout.write(f"  owner        : {owner_user.username} <{owner_user.email}> sub={owner_user.oidc_sub}")
        self.stdout.write(f"  non-owner    : {non_owner_user.username} <{non_owner_user.email}> sub={non_owner_user.oidc_sub}")
        self.stdout.write(f"  app slug     : {opts['app_slug']}")
        self.stdout.write(f"  app URL      : https://{app_hostname}")
        self.stdout.write(f"  auth URL     : https://auth.{opts['hosted_zone']}")
        self.stdout.write(f"  pdp-mock URL : {pdp_mock_url}")

        # Env may not exist yet; build a placeholder so later phases don't crash.
        env = models.Environment.objects.filter(
            aws_account=aws_account, slug=opts["env_slug"],
        ).first()
        return TestContext(
            aws_account=aws_account,
            organization=organization,
            env_slug=opts["env_slug"],
            env=env,  # may be None, _phase_create_env handles it
            owner_user=owner_user,
            non_owner_user=non_owner_user,
            hosted_zone=opts["hosted_zone"],
            region=opts["region"],
            app_slug=opts["app_slug"],
            app_hostname=app_hostname,
            pdp_mock_url=pdp_mock_url,
        )

    def _resolve_user(self, organization: models.Organization, identifier: str, role: str) -> models.User:
        """Resolve a User by username, email, or oidc_sub — must belong to *organization*."""
        from django.db.models import Q
        membership = models.OrganizationMembership.objects.filter(
            organization=organization,
        ).filter(
            Q(user__username=identifier) | Q(user__email__iexact=identifier) | Q(user__oidc_sub=identifier),
        ).select_related("user").first()
        if membership is None:
            raise CommandError(
                f"No {role} user matching {identifier!r} found as a member of org {organization.slug!r}.",
            )
        return membership.user

    # ------------------------------------------------------------------
    # Phase 1 — seed the PA owner policy + username identity attributes
    # ------------------------------------------------------------------

    def _phase_seed_abac(self, ctx: TestContext) -> None:
        self._banner("Phase 1: seed-abac")

        # bootstrap_organization is idempotent and now installs the PA owner policy.
        # It needs an admin user — pick the first admin in the org; if none, use
        # the owner (who probably is).
        admin_membership = models.OrganizationMembership.objects.filter(
            organization=ctx.organization, role=models.OrganizationMembership.Role.ADMIN,
        ).select_related("user").first()
        admin_user = admin_membership.user if admin_membership else ctx.owner_user
        abac.bootstrap_organization(organization=ctx.organization, admin_user=admin_user)
        self.stdout.write(f"  bootstrapped ABAC on org {ctx.organization.slug!r} (admin: {admin_user.username})")

        for user in (ctx.owner_user, ctx.non_owner_user):
            models.IdentityAttribute.objects.get_or_create(
                organization=ctx.organization, user=user,
                key="username", value=user.username,
            )
        self.stdout.write("  ensured username identity attributes on owner + non-owner")

        policy = models.Policy.objects.filter(
            organization=ctx.organization, name="Personal Assistant: owner access",
        ).first()
        if policy is None:
            raise CommandError("PA owner policy still missing after bootstrap — unexpected.")
        self.stdout.write(self.style.SUCCESS("  PA owner policy present"))

    # ------------------------------------------------------------------
    # Phase 2 — create + provision env if missing
    # ------------------------------------------------------------------

    def _phase_create_env(self, ctx: TestContext) -> None:
        self._banner("Phase 2: create-env")

        env = models.Environment.objects.filter(
            aws_account=ctx.aws_account, slug=ctx.env_slug,
        ).first()
        if env is None:
            env = models.Environment.objects.create(
                aws_account=ctx.aws_account,
                name=f"Policy-proxy E2E ({ctx.env_slug})",
                slug=ctx.env_slug,
                aws_region=ctx.region,
                shared_alb_hosted_zone=ctx.hosted_zone,
                status=models.Environment.Status.PENDING,
                status_message="Created by policy_proxy_e2e_test",
            )
            self.stdout.write(f"  created environment {env.slug!r}")
        else:
            self.stdout.write(f"  environment {env.slug!r} already exists (status={env.status})")

        ctx.env = env

        if env.status == models.Environment.Status.READY:
            self.stdout.write(self.style.SUCCESS("  environment is READY; skipping provisioning"))
            if env.shared_alb_hosted_zone != ctx.hosted_zone:
                raise CommandError(
                    f"Env {env.slug!r} has hosted zone "
                    f"{env.shared_alb_hosted_zone!r} but test expects {ctx.hosted_zone!r}. "
                    f"Choose a different --env-slug or tear down this env first.",
                )
            return

        self.stdout.write("  running environment_provisioning_executor.run_provisioning inline...")
        success = environment_provisioning_executor.run_provisioning(str(env.id))
        env.refresh_from_db()
        if not success or env.status != models.Environment.Status.READY:
            raise CommandError(
                f"Environment provisioning failed (status={env.status}): {env.status_message}",
            )
        self.stdout.write(self.style.SUCCESS(f"  environment provisioned (status={env.status})"))

    # ------------------------------------------------------------------
    # Phase 3 — deploy the mock PDP
    # ------------------------------------------------------------------

    def _phase_deploy_pdp_mock(self, ctx: TestContext) -> None:
        self._banner("Phase 3: pdp-mock")
        self._hydrate_env(ctx)

        from aws_cdk import App

        session = self._assumed_role_session(ctx)

        # Resolve the hosted zone ID up-front (the stack needs it, not a Fn.import_value).
        hosted_zone_id = route53_utils.get_hosted_zone_id(session=session, hosted_zone_name=ctx.hosted_zone)
        if not hosted_zone_id:
            raise CommandError(f"Hosted zone {ctx.hosted_zone!r} not found in the target account.")

        cdk_app = App(outdir=str(cdk_utils.CDK_OUT_DIR))
        stack_name = f"devopshero-{ctx.env.slug}-pdp-mock"
        PdpMockLambdaStack(
            cdk_app, stack_name,
            inputs=PdpMockLambdaInputs(
                env_slug=ctx.env.slug,
                env_domain=ctx.hosted_zone,
                shared_alb_https_listener_arn=Fn.import_value(
                    f"devopshero-{ctx.env.slug}-shared-alb-https-listener-arn",
                ),
                shared_alb_security_group_id=Fn.import_value(
                    f"devopshero-{ctx.env.slug}-shared-alb-sg-id",
                ),
                shared_hosted_zone_id=hosted_zone_id,
                shared_hosted_zone_name=ctx.hosted_zone,
                allowed_usernames=[ctx.owner_user.username],
            ),
        )
        assembly_dir = cdk_utils.synth_cdk_app(cdk_app)
        ok = cdk_utils.deploy_from_assembly(
            assembly_dir=assembly_dir, session=session, stack_names=[stack_name],
        )
        if not ok:
            raise CommandError("pdp-mock lambda deploy failed")

        # Sanity check the deployed endpoint accepts traffic.
        self._wait_for_pdp_mock_live(ctx)

    def _wait_for_pdp_mock_live(self, ctx: TestContext, timeout: int = 120) -> None:
        """Poll the mock PDP until it returns 200. ALB target health takes a few seconds."""
        self.stdout.write(f"  waiting for {ctx.pdp_mock_url} to respond...")
        body = json.dumps({
            "app_id": "x", "oidc_sub": "x", "username": ctx.owner_user.username, "path": "/",
        }).encode()
        deadline = time.time() + timeout
        last_exc: str = ""
        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    ctx.pdp_mock_url, data=body,
                    headers={"content-type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        data = json.loads(response.read())
                        if data.get("decision") == "allow":
                            self.stdout.write(self.style.SUCCESS("  pdp-mock returns allow for owner"))
                            return
                        last_exc = f"unexpected response: {data}"
            except Exception as exc:
                last_exc = str(exc)
            time.sleep(5)
        raise CommandError(f"pdp-mock did not come up in {timeout}s: {last_exc}")

    # ------------------------------------------------------------------
    # Phase 4 — deploy the PA (inline)
    # ------------------------------------------------------------------

    def _phase_deploy_pa(self, ctx: TestContext) -> None:
        self._banner("Phase 4: deploy")
        self._hydrate_env(ctx)

        template = models.AppTemplate.objects.get(slug="hermes-personal")
        workspace = models.Workspace.objects.filter(organization=ctx.organization).first()
        if workspace is None:
            raise CommandError(f"No workspace found in org {ctx.organization.slug!r}.")

        # Remove any stale app with the same slug before re-running.
        existing = models.App.objects.filter(organization=ctx.organization, slug=ctx.app_slug).first()
        if existing is not None:
            self.stdout.write(
                self.style.WARNING(f"  app {ctx.app_slug!r} already exists; reusing."),
            )
            ctx.app = existing
        else:
            overrides = _minimum_runtime_overrides(template)
            self.stdout.write(f"  creating app + blueprint + deployment via deploy_from_template...")
            deployment = async_to_sync(template_deploy_service.deploy_from_template)(
                template=template,
                organization=ctx.organization,
                workspace=workspace,
                environment=ctx.env,
                app_name=f"Policy-proxy E2E PA ({ctx.app_slug})",
                app_slug=ctx.app_slug,
                created_by=ctx.owner_user,
                runtime_variable_overrides=overrides,
                owner_username=ctx.owner_user.username,
            )
            ctx.app = deployment.app

        # Run the deployment inline. Point the policy proxy at the mock PDP via env var;
        # deploy_app.py reads DOH_PDP_URL at AppStack construction time.
        import os
        original_pdp_url = os.environ.get("DOH_PDP_URL")
        os.environ["DOH_PDP_URL"] = ctx.pdp_mock_url
        try:
            latest_deployment = models.Deployment.objects.filter(
                app=ctx.app, environment=ctx.env,
            ).order_by("-created_at").first()
            if latest_deployment is None:
                raise CommandError("No deployment found for the app; unexpected.")
            if latest_deployment.status == models.Deployment.Status.SUCCEEDED:
                self.stdout.write(self.style.SUCCESS(f"  deployment {latest_deployment.id} already succeeded."))
                return
            self.stdout.write(f"  running app_deployment_executor.run_deployment({latest_deployment.id}) inline...")
            success = app_deployment_executor.run_deployment(str(latest_deployment.id))
            latest_deployment.refresh_from_db()
            if not success:
                raise CommandError(
                    f"Deployment failed (status={latest_deployment.status}): "
                    f"{latest_deployment.status_message}",
                )
            self.stdout.write(self.style.SUCCESS(f"  deployment succeeded: {latest_deployment.service_url}"))
        finally:
            if original_pdp_url is None:
                os.environ.pop("DOH_PDP_URL", None)
            else:
                os.environ["DOH_PDP_URL"] = original_pdp_url

    # ------------------------------------------------------------------
    # Phase 5 — smoke tests against the deployed endpoints
    # ------------------------------------------------------------------

    def _phase_smoke(self, ctx: TestContext) -> None:
        self._banner("Phase 5: smoke")

        jwks_url = f"https://auth.{ctx.hosted_zone}/.well-known/jwks.json"
        self._expect_http(jwks_url, expected_status=200, tag="JWKS endpoint")

        self._expect_http(
            f"https://{ctx.app_hostname}/__policy_proxy/healthz",
            expected_status=200, tag="policy-proxy /__policy_proxy/healthz",
        )

        self._expect_http(
            f"https://{ctx.app_hostname}/",
            expected_status=302,
            tag="unauthenticated / should redirect to auth",
            location_must_start_with=f"https://auth.{ctx.hosted_zone}/start",
        )

    # ------------------------------------------------------------------
    # Phase 6 — mint owner + non-owner cookies
    # ------------------------------------------------------------------

    def _phase_mint(self, ctx: TestContext) -> dict[str, str]:
        self._banner("Phase 6: mint")
        self._hydrate_env(ctx)

        from devopshero_app.management.commands import policy_proxy_mint_cookie as mint_mod

        owner_jwt = mint_mod.mint_session_jwt(
            aws_account_name=ctx.aws_account.name,
            env_slug=ctx.env.slug,
            username=ctx.owner_user.username,
            oidc_sub=ctx.owner_user.oidc_sub,
            email=ctx.owner_user.email or "",
            ttl_seconds=3600,
            tamper=False,
        )
        non_owner_jwt = mint_mod.mint_session_jwt(
            aws_account_name=ctx.aws_account.name,
            env_slug=ctx.env.slug,
            username=ctx.non_owner_user.username,
            oidc_sub=ctx.non_owner_user.oidc_sub,
            email=ctx.non_owner_user.email or "",
            ttl_seconds=3600,
            tamper=False,
        )
        tampered_jwt = mint_mod.mint_session_jwt(
            aws_account_name=ctx.aws_account.name,
            env_slug=ctx.env.slug,
            username=ctx.owner_user.username,
            oidc_sub=ctx.owner_user.oidc_sub,
            email=ctx.owner_user.email or "",
            ttl_seconds=3600,
            tamper=True,
        )
        self.stdout.write("  minted owner, non-owner, and tampered JWTs")
        return {"owner": owner_jwt, "non_owner": non_owner_jwt, "tampered": tampered_jwt}

    # ------------------------------------------------------------------
    # Phase 7 — verify policy-proxy behavior against each cookie
    # ------------------------------------------------------------------

    def _phase_verify(self, ctx: TestContext, cookies: dict[str, str]) -> None:
        self._banner("Phase 7: verify")

        auth_start_url = f"https://auth.{ctx.hosted_zone}/start"
        results: list[tuple[str, str]] = []

        # Owner — policy proxy should forward through to the upstream. We
        # don't care what status the upstream returns (Hermes WebUI 302s its
        # own /login for unauthenticated WebUI sessions, for example); we
        # only care that the proxy did NOT 302 to auth.
        status = self._probe(
            url=f"https://{ctx.app_hostname}/",
            cookie=f"doh_session={cookies['owner']}",
        )
        self._assert_not_auth_redirect(
            status=status, tag="owner cookie", auth_start_url=auth_start_url,
        )
        results.append(("owner", f"HTTP {status.code} -> proxied (not auth redirect)"))

        # Non-owner — PDP denies, policy proxy returns 403 directly.
        status = self._probe(
            url=f"https://{ctx.app_hostname}/",
            cookie=f"doh_session={cookies['non_owner']}",
        )
        if status.code != 403:
            raise CommandError(
                f"non-owner cookie: expected 403, got {status.code}",
            )
        results.append(("non-owner", f"HTTP 403"))

        # Tampered — policy proxy treats as missing/invalid cookie and 302s to auth.
        status = self._probe(
            url=f"https://{ctx.app_hostname}/",
            cookie=f"doh_session={cookies['tampered']}",
        )
        if status.code != 302 or not status.location.startswith(auth_start_url):
            raise CommandError(
                f"tampered cookie: expected 302 to auth/start, got {status.code} location={status.location!r}",
            )
        results.append(("tampered", f"HTTP 302 -> {status.location[:60]}..."))

        # No cookie — same as tampered, 302 to auth.
        status = self._probe(
            url=f"https://{ctx.app_hostname}/",
            cookie=None,
        )
        if status.code != 302 or not status.location.startswith(auth_start_url):
            raise CommandError(
                f"no cookie: expected 302 to auth/start, got {status.code} location={status.location!r}",
            )
        results.append(("no cookie", f"HTTP 302 -> {status.location[:60]}..."))

        self.stdout.write(self.style.SUCCESS("\n  all four request flows behaved as expected:"))
        for case, actual in results:
            self.stdout.write(f"    {case:<12}  {actual}")

    def _assert_not_auth_redirect(self, status, tag: str, auth_start_url: str) -> None:
        if status.code == 302 and status.location.startswith(auth_start_url):
            raise CommandError(
                f"{tag}: policy proxy redirected to auth (expected passthrough). "
                f"status={status.code} location={status.location!r}",
            )
        self.stdout.write(f"  {tag}: HTTP {status.code} (proxied through)")

    # ------------------------------------------------------------------
    # Phase 8 — tear down the PA (env-wide infra left in place)
    # ------------------------------------------------------------------

    def _phase_cleanup(self, ctx: TestContext) -> None:
        self._banner("Phase 8: cleanup")

        if ctx.app is None:
            self.stdout.write("  no app to tear down.")
            return

        latest = models.Deployment.objects.filter(app=ctx.app).order_by("-created_at").first()
        if latest is None:
            self.stdout.write("  no deployment to tear down.")
            return

        if latest.status in (
            models.Deployment.Status.TEARDOWN_PENDING,
            models.Deployment.Status.TEARING_DOWN,
            models.Deployment.Status.TORN_DOWN,
        ):
            self.stdout.write(f"  deployment already {latest.status}")
            return

        latest.status = models.Deployment.Status.TEARDOWN_PENDING
        latest.status_message = "Teardown triggered by policy_proxy_e2e_test"
        latest.save(update_fields=["status", "status_message", "updated_at"])
        self.stdout.write(
            self.style.WARNING(
                f"  marked deployment {latest.id} TEARDOWN_PENDING; run the job worker to tear down.",
            )
        )
        self.stdout.write("  env-wide infrastructure (auth lambda, pdp-mock, policy-proxy ECR) left in place.")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _hydrate_env(self, ctx: TestContext) -> None:
        """Later phases may be run in isolation — make sure ctx.env is populated."""
        if ctx.env is not None:
            return
        env = models.Environment.objects.filter(
            aws_account=ctx.aws_account, slug=ctx.env_slug,
        ).first()
        if env is None:
            raise CommandError(
                f"Environment {ctx.env_slug!r} not found. Run from an earlier phase "
                f"(e.g. --skip-until-phase create-env) to create it first.",
            )
        ctx.env = env

    def _assumed_role_session(self, ctx: TestContext):
        return iam_utils.get_assumed_role_session(
            access_key=settings.DOH_AWS_ACCESS_KEY,
            secret_key=settings.DOH_AWS_SECRET_KEY,
            account_id=ctx.aws_account.aws_account_id,
            external_id=str(ctx.aws_account.external_id),
            region=ctx.env.aws_region if ctx.env else ctx.region,
        )

    def _banner(self, label: str) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"=== {label} ==="))

    @dataclass
    class _ProbeResult:
        code: int
        location: str

    def _probe(self, url: str, cookie: str | None) -> "_ProbeResult":
        """Send a GET, return (status_code, location_header). Don't follow redirects."""
        req = urllib.request.Request(url, method="GET")
        if cookie:
            req.add_header("Cookie", cookie)

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(req, timeout=10) as response:
                return Command._ProbeResult(
                    code=response.status,
                    location=response.headers.get("Location", "") or "",
                )
        except urllib.error.HTTPError as e:
            return Command._ProbeResult(
                code=e.code,
                location=(e.headers.get("Location", "") if e.headers else "") or "",
            )

    def _expect_http(
        self,
        url: str,
        expected_status: int,
        tag: str,
        cookie: str | None = None,
        location_must_start_with: str | None = None,
    ) -> str:
        """Assert an exact HTTP status. Used by Phase 5 smoke tests."""
        status = self._probe(url=url, cookie=cookie)
        if status.code != expected_status:
            raise CommandError(f"{tag}: expected {expected_status}, got {status.code} (url={url})")
        if location_must_start_with and not status.location.startswith(location_must_start_with):
            raise CommandError(
                f"{tag}: Location header {status.location!r} did not start with {location_must_start_with!r}",
            )
        location_note = f" -> {status.location}" if status.location else ""
        self.stdout.write(f"  {tag}: HTTP {status.code}{location_note}")
        return f"HTTP {status.code}{location_note}"


def _minimum_runtime_overrides(template: models.AppTemplate) -> dict[str, str]:
    """Build the smallest override dict that lets hermes-personal boot.

    The Hermes template declares a lot of configurable variables as user-editable.
    Most are optional; a few are required-without-default. We fill required
    strings with placeholder values so the template-deploy form's validation
    passes. Per-variable defaults come from the template; we only override
    required ones that have no default value.
    """
    overrides: dict[str, str] = {}
    for container in template.containers or []:
        for var in container.get("configurable_variables") or []:
            if not var.get("user_editable"):
                continue
            if not var.get("required"):
                continue
            if var.get("value") or var.get("default_value"):
                continue
            # Required + no value + no default — supply a harmless placeholder so
            # the test deploy actually starts. The container may fail later if the
            # placeholder is nonsensical, but that's visible in the smoke tests.
            overrides[var["name"]] = "policy-proxy-e2e-placeholder"
    return overrides
