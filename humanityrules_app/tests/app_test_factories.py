"""Shared factories for AppTemplate rows used across the test suite.

Apps are template-sourced: every App carries a non-null source_template. Tests
that don't care about the template's container shape use make_source_template();
those that assert on container wiring build their own spec via make_app_template().
"""

import uuid

from humanityrules_app.models import AppTemplate


def make_app_template(slug: str, containers: list[dict], alb_target_container: str | None) -> AppTemplate:
    """A valid AppTemplate carrying the given container spec."""
    return AppTemplate.objects.create(
        name=slug,
        slug=slug,
        description="Test template",
        icon="app",
        category="test",
        cpu=256,
        memory=512,
        alb_target_container=alb_target_container,
        containers=containers,
        is_active=True,
    )


def make_source_template() -> AppTemplate:
    """A minimal single-container template for Apps whose template shape is irrelevant."""
    return make_app_template(
        slug=f"tmpl-{uuid.uuid4().hex[:12]}",
        containers=[
            {
                "name": "app",
                "image_source": "template",
                "template_path": "hermes_agent",
                "container_port": 8000,
                "health_check_path": "/health",
            },
        ],
        alb_target_container="app",
    )
