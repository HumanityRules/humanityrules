"""
Prepare the Meridian Systems org for demo video recording.

Creates fake deployment data so all Meridian apps appear deployed and healthy.
This is a video-specific hack — not generic seed data.

Assumes seed_test_apps has already run.

Usage:
    uv run manage.py seed_prepare_demo
    uv run manage.py seed_prepare_demo --reset
"""

import hashlib
import secrets
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from devopshero_app.models import (
    App,
    AppPermissions,
    Deployment,
    DeploymentBlueprint,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
    Organization,
    Policy,
    User,
)


MERIDIAN_SLUG = "meridian-systems"
HOSTED_ZONE = "meridian.devopshero.com"
SEED_EMAIL_DOMAIN = "meridiansystems.com"

GROUPS_FROM_SEED_TEST_GROUPS = {
    "Engineering", "Data Science", "Security", "DevOps", "Product",
    "QA", "Contractors", "Admins", "Auditors",
}

ATTRIBUTE_OVERRIDES = {
    "Finance": {"clearance": "internal"},
    "Contractors": {"clearance": "external"},
}

DEMO_POLICIES = [
    {
        "name": "Finance team: workspace access",
        "resource_type": Policy.ResourceType.WORKSPACE,
        "identity_conditions": [{"key": "department", "value": "finance"}, {"key": "clearance", "value": "internal"}],
        "resource_conditions": [{"key": "workspace-name", "value": "finance"}],
        "actions": ["workspace:edit"],
    },
    {
        "name": "Production deploys: internal admins only",
        "resource_type": Policy.ResourceType.ENVIRONMENT,
        "identity_conditions": [{"key": "org-role", "value": "admin"}, {"key": "clearance", "value": "internal"}],
        "resource_conditions": [{"key": "environment-name", "value": "production"}],
        "actions": ["environment:deploy"],
    },
    {
        "name": "External contractors: view only",
        "resource_type": Policy.ResourceType.WORKSPACE,
        "identity_conditions": [{"key": "clearance", "value": "external"}],
        "resource_conditions": [{"key": "*", "value": "*"}],
        "actions": ["workspace:view"],
    },
]

APP_DEPLOY_CONFIGS = {
    "prism-scoring-engine": {
        "cpu": 1024,
        "memory": 2048,
        "subdomain": "prism-scoring-engine",
        "env_vars": [
            {"name": "MODEL_VERSION", "value": "v2.4.1"},
            {"name": "BATCH_SIZE", "value": "64"},
        ],
        "secrets": {"API_KEY": "sk-prod-xxxxx", "DB_PASSWORD": "xxxxx"},
        "commit_message": "feat: add batch prediction endpoint",
        "permissions": [
            {"service": "S3", "effect": "Allow", "access_levels": ["Read"], "resources": ["arn:aws:s3:::meridian-ml-models/*"]},
            {"service": "DynamoDB", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:dynamodb:us-east-1:111222333444:table/feature-store"]},
        ],
    },
    "catalyst-etl-runner": {
        "cpu": 512,
        "memory": 1024,
        "branch": "dev",
        "deployed_days_ago": 74,
        "subdomain": "catalyst-etl-runner",
        "env_vars": [
            {"name": "QUEUE_URL", "value": "https://sqs.us-east-1.amazonaws.com/111222333444/etl-jobs"},
            {"name": "CONCURRENCY", "value": "4"},
        ],
        "secrets": {"DB_PASSWORD": "xxxxx"},
        "commit_message": "fix: handle empty SQS messages gracefully",
        "permissions": [
            {"service": "SQS", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:sqs:us-east-1:111222333444:etl-jobs"]},
            {"service": "S3", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:s3:::meridian-data-lake/*"]},
        ],
    },
    "ingestion-service": {
        "cpu": 512,
        "memory": 1024,
        "deployed_days_ago": 63,
        "subdomain": "ingestion-service",
        "env_vars": [
            {"name": "UPLOAD_BUCKET", "value": "meridian-uploads"},
            {"name": "MAX_FILE_SIZE_MB", "value": "100"},
        ],
        "secrets": {"API_KEY": "sk-prod-xxxxx"},
        "commit_message": "chore: bump dependencies, add CSV parser",
        "permissions": [
            {"service": "S3", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:s3:::meridian-uploads/*"]},
        ],
    },
    "compass-account-api": {
        "cpu": 512,
        "memory": 1024,
        "subdomain": "compass-account-api",
        "env_vars": [
            {"name": "DATABASE_URL", "value": "postgresql://portal_db:5432/portal"},
            {"name": "CORS_ORIGINS", "value": "https://horizon-portal.meridian.devopshero.com"},
        ],
        "secrets": {"DB_PASSWORD": "xxxxx", "JWT_SECRET": "xxxxx"},
        "commit_message": "feat: add account deactivation endpoint",
        "permissions": [
            {"service": "Secrets Manager", "effect": "Allow", "access_levels": ["Read"], "resources": ["arn:aws:secretsmanager:us-east-1:111222333444:secret:devopshero/compass-account-api/*"]},
        ],
    },
    "horizon-portal": {
        "cpu": 256,
        "memory": 512,
        "branch": "staging",
        "subdomain": "horizon-portal",
        "env_vars": [
            {"name": "NEXT_PUBLIC_API_URL", "value": "https://compass-account-api.meridian.devopshero.com"},
        ],
        "secrets": None,
        "commit_message": "feat: add dark mode toggle",
        "permissions": [],
    },
    "atlas-query-gateway": {
        "cpu": 512,
        "memory": 1024,
        "subdomain": "atlas-query-gateway",
        "env_vars": [
            {"name": "UPSTREAM_SERVICES", "value": "compass-account-api,prism-scoring-engine"},
            {"name": "RATE_LIMIT_RPS", "value": "500"},
        ],
        "secrets": {"API_KEY": "sk-prod-xxxxx"},
        "commit_message": "feat: add query caching layer",
        "permissions": [],
    },
    "ledgerline-expense-tracker": {
        "cpu": 512,
        "memory": 1024,
        "subdomain": "ledgerline-expense-tracker",
        "env_vars": [
            {"name": "DATABASE_URL", "value": "postgresql://ledger_db:5432/expenses"},
            {"name": "ALLOWED_HOSTS", "value": "ledgerline-expense-tracker.meridian.devopshero.com"},
        ],
        "secrets": {"DB_PASSWORD": "xxxxx", "DJANGO_SECRET_KEY": "xxxxx"},
        "commit_message": "feat: add receipt OCR upload",
        "permissions": [
            {"service": "S3", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:s3:::meridian-expense-receipts/*"]},
            {"service": "Textract", "effect": "Allow", "access_levels": ["Read"], "resources": ["*"]},
        ],
    },
    "reconciliation-runner": {
        "cpu": 256,
        "memory": 512,
        "branch": "dev",
        "deployed_days_ago": 89,
        "subdomain": "reconciliation-runner",
        "env_vars": [
            {"name": "SCHEDULE", "value": "0 2 * * *"},
            {"name": "REPORT_BUCKET", "value": "meridian-finance-reports"},
        ],
        "secrets": {"DB_PASSWORD": "xxxxx"},
        "commit_message": "fix: correct timezone in nightly reconciliation",
        "permissions": [
            {"service": "S3", "effect": "Allow", "access_levels": ["Read", "Write"], "resources": ["arn:aws:s3:::meridian-finance-reports/*"]},
        ],
    },
}


def _fake_commit_sha(app_slug: str) -> str:
    """Deterministic fake 40-char SHA based on app slug."""
    return hashlib.sha256(f"demo-commit-{app_slug}".encode()).hexdigest()[:40]


def _fake_image_tag(app_slug: str) -> str:
    return f"{app_slug}-main-20260405T1430"


def _fake_image_uri(app_slug: str, env_slug: str) -> str:
    return f"111222333444.dkr.ecr.us-east-1.amazonaws.com/doh/{env_slug}/{app_slug}:{_fake_image_tag(app_slug=app_slug)}"


def _fake_service_url(subdomain: str) -> str:
    return f"https://{subdomain}.{HOSTED_ZONE}"


class Command(BaseCommand):
    help = "Prepare Meridian Systems org for demo video — make all apps look deployed and healthy"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--reset", action="store_true", help="Delete demo artifacts before re-creating")

    def handle(self, *args, **options) -> None:
        try:
            org = Organization.objects.get(slug=MERIDIAN_SLUG)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization '{MERIDIAN_SLUG}' not found. Run seed_test_apps first."
            )

        if options["reset"]:
            self._reset(org=org)

        self._prepare_security(org=org)
        self._set_hosted_zones(org=org)
        self._create_deployments(org=org)

        self.stdout.write(self.style.SUCCESS("Demo prep complete."))

    def _reset(self, org: Organization) -> None:
        apps = App.objects.filter(organization=org)
        dep_count = Deployment.objects.filter(app__in=apps).delete()[0]
        bp_count = DeploymentBlueprint.objects.filter(app__in=apps).delete()[0]
        perm_count = AppPermissions.objects.filter(app__in=apps).delete()[0]
        self.stdout.write(f"  Reset: deleted {dep_count} deployments, {bp_count} blueprints, {perm_count} permissions")

        for env in Environment.objects.filter(aws_account__organization=org):
            env.shared_alb_hosted_zone = ""
            env.save()
        self.stdout.write("  Reset: cleared hosted zones")

        user_count = User.objects.filter(
            Q(email__iendswith=f"@{SEED_EMAIL_DOMAIN}"), current_organization=org,
        ).delete()[0]
        group_count = Group.objects.filter(
            organization=org, name__in=GROUPS_FROM_SEED_TEST_GROUPS,
        ).delete()[0]
        policy_count = Policy.objects.filter(organization=org, is_system=False).delete()[0]
        self.stdout.write(f"  Reset: deleted {user_count} users, {group_count} groups, {policy_count} custom policies")

        finance_group = Group.objects.filter(organization=org, name="Finance").first()
        if finance_group:
            GroupAttribute.objects.filter(group=finance_group, key="clearance").update(value="restricted")
            self.stdout.write("  Reset: reverted Finance clearance to 'restricted'")

    def _prepare_security(self, org: Organization) -> None:
        call_command("seed_test_groups", org=MERIDIAN_SLUG)

        for group_name, overrides in ATTRIBUTE_OVERRIDES.items():
            group = Group.objects.filter(organization=org, name=group_name).first()
            if group is None:
                continue
            for key, value in overrides.items():
                attr, created = GroupAttribute.objects.get_or_create(
                    group=group, key=key, defaults={"value": value},
                )
                if not created and attr.value != value:
                    attr.value = value
                    attr.save()
                    self.stdout.write(f"  Fixed {group_name} {key}: {attr.value} -> {value}")

        admin_user = User.objects.filter(email="vmendi@gmail.com").first()
        if admin_user:
            finance = Group.objects.filter(organization=org, name="Finance").first()
            if finance:
                GroupMembership.objects.get_or_create(group=finance, user=admin_user)

        has_seed_users = User.objects.filter(
            email__iendswith=f"@{SEED_EMAIL_DOMAIN}", current_organization=org,
        ).exists()
        if not has_seed_users:
            call_command(
                "seed_test_users",
                org=MERIDIAN_SLUG,
                email_domain=SEED_EMAIL_DOMAIN,
                count=100,
            )
        else:
            self.stdout.write(f"  Skipping user creation — @{SEED_EMAIL_DOMAIN} users already exist")

        for policy_data in DEMO_POLICIES:
            Policy.objects.get_or_create(
                organization=org,
                name=policy_data["name"],
                defaults={
                    "resource_type": policy_data["resource_type"],
                    "identity_conditions": policy_data["identity_conditions"],
                    "resource_conditions": policy_data["resource_conditions"],
                    "actions": policy_data["actions"],
                    "is_system": False,
                },
            )
        self.stdout.write(f"  Policies: {len(DEMO_POLICIES)} demo policies ensured")

    def _set_hosted_zones(self, org: Organization) -> None:
        for env in Environment.objects.filter(aws_account__organization=org):
            env.shared_alb_hosted_zone = HOSTED_ZONE
            env.save()
            self.stdout.write(f"  Hosted zone: {env.name} -> {HOSTED_ZONE}")

    def _create_deployments(self, org: Organization) -> None:
        now = timezone.now()
        apps = App.objects.filter(organization=org).select_related("workspace", "repository")

        for idx, app in enumerate(apps.order_by("name")):
            config = APP_DEPLOY_CONFIGS.get(app.slug)
            if config is None:
                self.stdout.write(self.style.WARNING(f"  Skipping {app.slug} — no deploy config"))
                continue

            env = Environment.objects.filter(
                aws_account__organization=org,
                slug="production",
            ).first()
            if env is None:
                self.stdout.write(self.style.WARNING("  No production environment found, skipping"))
                return

            if DeploymentBlueprint.objects.filter(app=app, environment=env).exists():
                self.stdout.write(f"  Skipping {app.slug} — blueprint already exists for {env.name}")
                continue

            branch = config.get("branch", "main")

            if app.branch != branch:
                app.branch = branch
                app.save()

            blueprint = DeploymentBlueprint.objects.create(
                app=app,
                environment=env,
                status=DeploymentBlueprint.Status.ACTIVE,
                branch=branch,
                cpu=config["cpu"],
                memory=config["memory"],
                environment_variables=config["env_vars"],
                app_secrets=config["secrets"],
                subdomain=config["subdomain"],
                created_by=app.created_by,
            )

            days_ago = config.get("deployed_days_ago", 1 + idx)
            deployed_at = now - timedelta(days=days_ago, hours=2 + idx * 3, minutes=17 + idx * 13)
            commit_sha = _fake_commit_sha(app_slug=app.slug)
            image_tag = _fake_image_tag(app_slug=app.slug)

            has_url = app.app_type == App.AppType.WEB
            service_url = _fake_service_url(subdomain=config["subdomain"]) if has_url else ""

            deployment = Deployment.objects.create(
                blueprint=blueprint,
                app=app,
                environment=env,
                git_ref=branch,
                git_commit_sha=commit_sha,
                git_commit_message=config["commit_message"],
                image_tag=image_tag,
                image_uri=_fake_image_uri(app_slug=app.slug, env_slug=env.slug),
                status=Deployment.Status.SUCCEEDED,
                subdomain=config["subdomain"] if has_url else "",
                service_url=service_url,
                started_at=deployed_at - timedelta(minutes=2),
                completed_at=deployed_at,
                created_by=app.created_by,
            )
            Deployment.objects.filter(pk=deployment.pk).update(created_at=deployed_at)

            if config["permissions"]:
                AppPermissions.objects.create(
                    app=app,
                    environment=env,
                    statements=config["permissions"],
                )

            self.stdout.write(
                f"  {app.name}: blueprint(active) + deployment(succeeded) -> {config['subdomain']}.{HOSTED_ZONE}"
            )
