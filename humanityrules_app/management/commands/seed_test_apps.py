"""
Seed mock organizations, workspaces, environments, and apps for UI testing.

Uses real GitHub repos from the vmendi account.

Usage:
    uv run manage.py seed_test_apps --user=vmendi@gmail.com
"""

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.services import abac_service
from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    Group,
    GroupAttribute,
    GroupMembership,
    Organization,
    Repository,
    User,
    Workspace,
)


def _github_repo(github_name: str) -> dict:
    """Build a repo dict from a vmendi/<github_name> GitHub repo."""
    return {
        "name": github_name,
        "full_name": f"vmendi/{github_name}",
        "provider": "github",
        "clone_url": f"https://github.com/vmendi/{github_name}.git",
    }


MOCK_ORGS = [
    {
        "name": "Meridian Systems",
        "slug": "meridian-systems",
        "aws_accounts": [
            {
                "name": "Production AWS",
                "aws_account_id": "111222333444",
                "role_arn": "arn:aws:iam::111222333444:role/HumanityRulesRole",
                "status": AWSAccount.Status.CONNECTED,
                "environments": [
                    {"name": "production", "slug": "production", "aws_region": "us-east-1", "status": Environment.Status.READY},
                    {"name": "staging", "slug": "staging", "aws_region": "us-west-2", "status": Environment.Status.READY},
                    {"name": "dev", "slug": "dev", "aws_region": "us-east-1", "status": Environment.Status.READY},
                ],
            },
        ],
        "workspaces": [
            {"name": "Data Platform", "slug": "data-platform", "description": "ML models, data pipelines, and processing services"},
            {"name": "Internal Tools", "slug": "internal-tools", "description": "Internal dashboards, admin panels, and APIs"},
            {"name": "Finance", "slug": "finance", "description": "Expense tracking, reconciliation, and financial reporting"},
        ],
        "groups": [
            {
                "name": "Finance",
                "description": "Finance and billing",
                "attributes": [
                    ("department", "finance"),
                    ("clearance", "restricted"),
                    ("cost_center", "CC-400"),
                ],
            },
        ],
        "repositories": [
            _github_repo("job-processor"),
            _github_repo("file-processor"),
            _github_repo("fastapi-app"),
            _github_repo("nextjs-app"),
            _github_repo("graphql-api"),
            _github_repo("django-postgres-app"),
            _github_repo("scheduled-tasks"),
        ],
        "apps": [
            {
                "workspace_slug": "data-platform",
                "repo_full_name": "vmendi/job-processor",
                "name": "Catalyst ETL Runner",
                "slug": "catalystetlrunner",
                "build_strategy": "dockerfile",
                "container_port": 8080,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "data-platform",
                "repo_full_name": "vmendi/file-processor",
                "name": "Ingestion Service",
                "slug": "ingestionservice",
                "build_strategy": "dockerfile",
                "container_port": 8000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "internal-tools",
                "repo_full_name": "vmendi/fastapi-app",
                "name": "Compass Account API",
                "slug": "compassaccountapi",
                "build_strategy": "dockerfile",
                "container_port": 8000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "internal-tools",
                "repo_full_name": "vmendi/nextjs-app",
                "name": "Horizon Portal",
                "slug": "horizonportal",
                "build_strategy": "nixpacks",
                "container_port": 3000,
                "health_check_path": "/",
            },
            {
                "workspace_slug": "internal-tools",
                "repo_full_name": "vmendi/graphql-api",
                "name": "Atlas Query Gateway",
                "slug": "atlasquerygateway",
                "build_strategy": "dockerfile",
                "container_port": 4000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "finance",
                "repo_full_name": "vmendi/scheduled-tasks",
                "name": "Reconciliation Runner",
                "slug": "reconciliationrunner",
                "build_strategy": "dockerfile",
                "container_port": 8080,
                "health_check_path": "/health",
            },
        ],
    },
    {
        "name": "Clearwater Capital",
        "slug": "clearwater-capital",
        "aws_accounts": [
            {
                "name": "Clearwater Cloud",
                "aws_account_id": "555666777888",
                "role_arn": "arn:aws:iam::555666777888:role/HumanityRulesRole",
                "status": AWSAccount.Status.CONNECTED,
                "environments": [
                    {"name": "prod", "slug": "prod", "aws_region": "eu-west-1", "status": Environment.Status.READY},
                    {"name": "dev", "slug": "dev", "aws_region": "eu-west-1", "status": Environment.Status.READY},
                ],
            },
        ],
        "workspaces": [
            {"name": "Analytics", "slug": "analytics", "description": "Dashboards, monitoring, and reporting tools"},
        ],
        "repositories": [
            _github_repo("django-postgres-app"),
            _github_repo("realtime-app"),
            _github_repo("admin-dashboard"),
            _github_repo("phoenix-app"),
            _github_repo("scheduled-tasks"),
            _github_repo("slack-bot"),
        ],
        "apps": [
            {
                "workspace_slug": "default",
                "repo_full_name": "vmendi/django-postgres-app",
                "name": "Clearbook Ledger",
                "slug": "clearbookledger",
                "build_strategy": "dockerfile",
                "container_port": 8000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "default",
                "repo_full_name": "vmendi/realtime-app",
                "name": "Ticker Stream",
                "slug": "tickerstream",
                "build_strategy": "dockerfile",
                "container_port": 8000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "default",
                "repo_full_name": "vmendi/slack-bot",
                "name": "Watchtower Alerts",
                "slug": "watchtoweralerts",
                "build_strategy": "dockerfile",
                "container_port": 8080,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "analytics",
                "repo_full_name": "vmendi/admin-dashboard",
                "name": "Lakeview Dashboard",
                "slug": "lakeviewdashboard",
                "build_strategy": "dockerfile",
                "container_port": 3000,
                "health_check_path": "/",
            },
            {
                "workspace_slug": "analytics",
                "repo_full_name": "vmendi/phoenix-app",
                "name": "Osprey Health Monitor",
                "slug": "ospreyhealthmonitor",
                "build_strategy": "dockerfile",
                "container_port": 4000,
                "health_check_path": "/health",
            },
            {
                "workspace_slug": "analytics",
                "repo_full_name": "vmendi/scheduled-tasks",
                "name": "Tideline Reports",
                "slug": "tidelinereports",
                "build_strategy": "dockerfile",
                "container_port": 8080,
                "health_check_path": "/health",
            },
        ],
    },
]


class Command(BaseCommand):
    help = "Seed mock organizations, workspaces, environments, and apps for UI testing"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--user", required=True, help="Email of the user to associate data with")
        parser.add_argument("--reset", action="store_true", help="Delete existing mock orgs before re-creating")

    def handle(self, *args, **options) -> None:
        email = options["user"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"User with email '{email}' not found")

        if options["reset"]:
            self._delete_mock_orgs()

        for org_data in MOCK_ORGS:
            self._create_org(user=user, org_data=org_data)

        self.stdout.write(self.style.SUCCESS("Mock data seeded successfully."))

    def _delete_mock_orgs(self) -> None:
        mock_slugs = {o["slug"] for o in MOCK_ORGS}
        for org_data in MOCK_ORGS:
            slug = org_data["slug"]
            try:
                org = Organization.objects.get(slug=slug)
                for user in User.objects.filter(current_organization=org):
                    other_org = (
                        Organization.objects.filter(memberships__user=user)
                        .exclude(slug__in=mock_slugs)
                        .first()
                    )
                    if other_org:
                        user.current_organization = other_org
                        user.save()
                    else:
                        user.delete()
                App.objects.filter(organization=org).delete()
                org.delete()
                self.stdout.write(f"  Deleted org: {slug}")
            except Organization.DoesNotExist:
                pass

    def _create_org(self, user: User, org_data: dict) -> None:
        if Organization.objects.filter(slug=org_data["slug"]).exists():
            self.stdout.write(f"  Skipping org '{org_data['slug']}' (already exists)")
            return

        org = Organization.objects.create(name=org_data["name"], slug=org_data["slug"])
        apps_need_default = any(a["workspace_slug"] == "default" for a in org_data["apps"])
        if not apps_need_default:
            Workspace.objects.filter(organization=org, slug="default").delete()
        self.stdout.write(f"  Created org: {org.name}")

        abac_service.bootstrap_organization(organization=org, admin_user=user)
        self.stdout.write(f"    Membership: {user.email} as admin (bootstrapped)")

        for ws_data in org_data["workspaces"]:
            Workspace.objects.create(
                organization=org,
                name=ws_data["name"],
                slug=ws_data["slug"],
                description=ws_data["description"],
                created_by=user,
            )
            self.stdout.write(f"    Workspace: {ws_data['name']}")

        for group_data in org_data.get("groups", []):
            group = Group.objects.create(
                organization=org,
                name=group_data["name"],
                description=group_data["description"],
            )
            for key, value in group_data["attributes"]:
                GroupAttribute.objects.create(group=group, key=key, value=value)
            GroupMembership.objects.create(group=group, user=user)
            attr_count = len(group_data["attributes"])
            self.stdout.write(f"    Group: {group_data['name']} ({attr_count} attributes, 1 member)")

        for acct_data in org_data["aws_accounts"]:
            acct = AWSAccount.objects.create(
                organization=org,
                name=acct_data["name"],
                aws_account_id=acct_data["aws_account_id"],
                role_arn=acct_data["role_arn"],
                status=acct_data["status"],
                created_by=user,
            )
            self.stdout.write(f"    AWS Account: {acct.name}")

            for env_data in acct_data["environments"]:
                Environment.objects.create(
                    aws_account=acct,
                    name=env_data["name"],
                    slug=env_data["slug"],
                    aws_region=env_data["aws_region"],
                    status=env_data["status"],
                )
                self.stdout.write(f"      Environment: {env_data['name']} ({env_data['status']})")

        for repo_data in org_data["repositories"]:
            Repository.objects.create(
                organization=org,
                provider=repo_data["provider"],
                name=repo_data["name"],
                full_name=repo_data["full_name"],
                clone_url=repo_data["clone_url"],
                default_branch="main",
            )
            self.stdout.write(f"    Repository: {repo_data['full_name']}")

        for app_data in org_data["apps"]:
            workspace = Workspace.objects.get(organization=org, slug=app_data["workspace_slug"])
            repository = Repository.objects.get(organization=org, full_name=app_data["repo_full_name"])
            environment = Environment.objects.filter(aws_account__organization=org).order_by("slug").first()
            App.objects.create(
                organization=org,
                workspace=workspace,
                environment=environment,
                repository=repository,
                name=app_data["name"],
                slug=app_data["slug"],
                build_strategy=app_data["build_strategy"],
                container_port=app_data["container_port"],
                health_check_path=app_data["health_check_path"],
                cpu=256,
                memory=512,
                created_by=user,
            )
            self.stdout.write(f"    App: {app_data['name']} -> {app_data['workspace_slug']}")
