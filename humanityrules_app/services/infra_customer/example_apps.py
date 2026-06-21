"""
Application configurations for all deployable apps.

Each function returns an AppConfig for a specific app.
"""

from pathlib import Path

from django.conf import settings

from . import appconfig


def _get_deployable_repos_path() -> Path:
    """Get path to the deployable-repos directory (sibling of project root)."""
    return (settings.BASE_DIR / ".." / "deployable-repos").resolve()


def get_simple_dashboard_config(env_slug: str) -> appconfig.AppConfig:
    """Return the AppConfig for simple-dashboard."""
    return appconfig.AppConfig(
        app_name="simple-dashboard",
        ecr_repo_name=_build_ecr_repo_name("simple-dashboard", env_slug),
        container_port=8501,
        cpu=256,
        memory=512,
        health_check_path="/_stcore/health",
        health_check_command='python -c "import urllib.request; urllib.request.urlopen(\'http://localhost:8501/_stcore/health\', timeout=5)" || exit 1',
        environment_variables=[
            {"name": "STREAMLIT_SERVER_PORT", "value": "8501"},
            {"name": "STREAMLIT_SERVER_ADDRESS", "value": "0.0.0.0"},
            {"name": "STREAMLIT_SERVER_HEADLESS", "value": "true"},
            {"name": "STREAMLIT_BROWSER_GATHER_USAGE_STATS", "value": "false"},
        ],
        app_source_path=_get_deployable_repos_path() / "simple_dashboard",
    )


# Registry of all available apps
APP_CONFIGS = {
    "simple-dashboard": get_simple_dashboard_config,
}


def _build_ecr_repo_name(app_slug: str, env_slug: str) -> str:
    """Build ECR repository name (app slugs are unique per org, environments are per-account, no collision)."""
    return f"humr/{env_slug}/{app_slug}"


def get_app_config(app_name: str, env_slug: str) -> appconfig.AppConfig:
    """Get the AppConfig for a named app."""
    if app_name not in APP_CONFIGS:
        available = ", ".join(APP_CONFIGS.keys())
        raise ValueError(f"Unknown app: {app_name}. Available apps: {available}")
    return APP_CONFIGS[app_name](env_slug=env_slug)
