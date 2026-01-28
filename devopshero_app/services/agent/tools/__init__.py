"""
Agent tools for the deployment assistant.

Tools provide the agent with capabilities to interact with
repositories, AWS accounts, and the deployment system.
"""

from .create_datastore import create_datastore, DatastoreSummary
from .list_apps import list_apps
from .list_apps import AppSummary as ListAppSummary
from .create_environment import create_environment
from .create_environment import EnvironmentSummary as CreateEnvironmentSummary
from .get_environment_status import get_environment_status, EnvironmentStatus, EnvironmentLogEntry
from .deploy_app import deploy_app, DeploymentSummary
from .get_deployment_status import get_deployment_status, DeploymentStatus, DeploymentLogEntry
from .initiate_aws_connection import initiate_aws_connection, AWSConnectionInfo
from .list_aws_accounts import list_aws_accounts, AWSAccountSummary
from .list_environments import list_environments
from .list_environments import EnvironmentSummary as ListEnvironmentSummary
from .list_hosted_zones import list_hosted_zones, HostedZoneSummary
from .list_repositories import list_repositories, RepositoryListItem
from .scan_repository import scan_repository, RepositoryScan
from .teardown_deployment import teardown_deployment, TeardownSummary

__all__ = [
    # App management
    "list_apps",
    "ListAppSummary",
    # Datastore management
    "create_datastore",
    "DatastoreSummary",
    # Environment management
    "create_environment",
    "CreateEnvironmentSummary",
    "get_environment_status",
    "EnvironmentStatus",
    "EnvironmentLogEntry",
    "list_environments",
    "ListEnvironmentSummary",
    # Deployment
    "deploy_app",
    "DeploymentSummary",
    "get_deployment_status",
    "DeploymentStatus",
    "DeploymentLogEntry",
    # AWS accounts
    "initiate_aws_connection",
    "AWSConnectionInfo",
    "list_aws_accounts",
    "AWSAccountSummary",
    "list_hosted_zones",
    "HostedZoneSummary",
    # Repository
    "scan_repository",
    "RepositoryScan",
    "list_repositories",
    "RepositoryListItem",
    # Teardown
    "teardown_deployment",
    "TeardownSummary",
]
