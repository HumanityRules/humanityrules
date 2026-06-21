"""Tests for humr_app_shell container-name resolution."""

from django.core.management.base import CommandError
from django.test import TestCase

from humanityrules_app.management.commands import humr_app_shell
from humanityrules_app.models import App, AppTemplate, Organization, Repository, Workspace


def _template(slug: str, containers: list[dict], alb_target_container: str | None) -> AppTemplate:
    return AppTemplate.objects.create(
        name=slug,
        slug=slug,
        description="Template",
        icon="app",
        category="test",
        cpu=1024,
        memory=2048,
        alb_target_container=alb_target_container,
        containers=containers,
        is_active=True,
    )


def _app(template: AppTemplate | None) -> App:
    org = Organization.objects.create(name="Org", slug=f"org-{App.objects.count()}")
    workspace = Workspace.objects.get(organization=org, slug="default")
    repo = Repository.objects.create(
        organization=org,
        full_name=f"template/{template.slug if template else 'legacy'}",
        name="Repo",
        clone_url="file:///tmp/repo",
        default_branch="main",
    )
    return App.objects.create(
        organization=org,
        workspace=workspace,
        repository=repo,
        source_template=template,
        name="My App",
        slug="my-app",
        app_type=App.AppType.WEB,
        build_strategy=App.BuildStrategy.DOCKERFILE,
        branch="main",
        container_port=8080,
        health_check_path="/health",
    )


class HumrAppShellContainerResolutionTests(TestCase):

    def test_legacy_app_defaults_to_app_slug(self) -> None:
        app = _app(template=None)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container=None)

        self.assertEqual(container_name, "my-app")

    def test_single_template_container_defaults_to_that_container(self) -> None:
        template = _template(
            slug="single",
            containers=[{"name": "app"}],
            alb_target_container="app",
        )
        app = _app(template=template)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container=None)

        self.assertEqual(container_name, "my-app-app")

    def test_multi_container_template_defaults_to_alb_target_container(self) -> None:
        template = _template(
            slug="multi",
            containers=[{"name": "hermes"}, {"name": "sidecar-mcp"}],
            alb_target_container="hermes",
        )
        app = _app(template=template)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container=None)

        self.assertEqual(container_name, "my-app-hermes")

    def test_requested_template_container_resolves_to_ecs_container_name(self) -> None:
        template = _template(
            slug="multi-explicit",
            containers=[{"name": "hermes"}, {"name": "sidecar-mcp"}],
            alb_target_container="hermes",
        )
        app = _app(template=template)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container="sidecar-mcp")

        self.assertEqual(container_name, "my-app-sidecar-mcp")

    def test_requested_policy_proxy_resolves_when_declared_as_container(self) -> None:
        template = _template(
            slug="policy-proxy",
            containers=[
                {"name": "hermes"},
                {"name": "policy-proxy", "image_source": "policy_proxy"},
            ],
            alb_target_container="policy-proxy",
        )
        app = _app(template=template)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container="policy-proxy")

        self.assertEqual(container_name, "my-app-policy-proxy")

    def test_requested_full_ecs_container_name_passes_through(self) -> None:
        template = _template(
            slug="full-name",
            containers=[{"name": "hermes"}],
            alb_target_container="hermes",
        )
        app = _app(template=template)

        container_name = humr_app_shell._resolve_ecs_container_name(app=app, requested_container="my-app-hermes")

        self.assertEqual(container_name, "my-app-hermes")

    def test_unknown_template_container_raises(self) -> None:
        template = _template(
            slug="unknown",
            containers=[{"name": "hermes"}],
            alb_target_container="hermes",
        )
        app = _app(template=template)

        with self.assertRaises(CommandError):
            humr_app_shell._resolve_ecs_container_name(app=app, requested_container="missing")
