"""
Application configurations for all deployable apps.

Each function returns an AppConfig for a specific app.
"""

from pathlib import Path

from appconfig import AppConfig, DatabaseConfig


def get_simple_dashboard_config() -> AppConfig:
    """Return the AppConfig for simple-dashboard."""
    return AppConfig(
        app_name="simple-dashboard",
        ecr_repo_name="devopshero/simple-dashboard",
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
        app_source_path=Path(__file__).parent.parent / "deployable_repos" / "simple_dashboard",
        domain_name="simple-dashboard.chsandbox.com",
        hosted_zone_name="chsandbox.com",
    )


def get_db_portal_config() -> AppConfig:
    """Return the AppConfig for db_portal."""
    return AppConfig(
        app_name="db-portal",
        ecr_repo_name="devopshero/db-portal",
        container_port=4000,
        cpu=512,
        memory=1024,
        health_check_path="/health",
        health_check_command=None,
        environment_variables=[
            {"name": "MIX_ENV", "value": "prod"},
            {"name": "PHX_SERVER", "value": "true"},
            {"name": "PORT", "value": "4000"},
            {"name": "PHX_HOST", "value": "dataengr.chsandbox.com"},
            {"name": "DISABLE_HTTPS", "value": "true"},
            {"name": "DISABLE_AUTH", "value": "true"},
            {"name": "RUN_SAMPLER", "value": "N"},
        ],
        app_source_path=Path(__file__).parent.parent / "deployable_repos" / "db_portal",
        domain_name="dataengr.chsandbox.com",
        hosted_zone_name="chsandbox.com",
        database_config=DatabaseConfig(name="db_portal_prod"),
        app_secrets={
            "slack_token": "disabled",
            "secret_key_base": None,
            "signing_salt": None,
        },
    )


# Registry of all available apps
APP_CONFIGS = {
    "simple-dashboard": get_simple_dashboard_config,
    "db-portal": get_db_portal_config,
}


def get_app_config(app_name: str) -> AppConfig:
    """Get the AppConfig for a named app."""
    if app_name not in APP_CONFIGS:
        available = ", ".join(APP_CONFIGS.keys())
        raise ValueError(f"Unknown app: {app_name}. Available apps: {available}")
    return APP_CONFIGS[app_name]()
