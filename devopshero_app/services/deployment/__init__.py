"""
Deployment service module.

Handles the execution of deployments by bridging Django models to CDK infrastructure.
"""

import sys
from pathlib import Path

# infra_customer/ is a sibling directory (not inside devopshero_app/), so Python can't
# find it by default. Django only adds the project root to sys.path, which makes
# devopshero_app and its children importable, but not sibling directories.
# We add infra_customer to sys.path so we can `import deploy_app`, `import appconfig`, etc.
#
# Alternatives to this sys.path hack:
# 1. Make infra_customer a Python package (add pyproject.toml, `uv pip install -e infra_customer/`)
# 2. Move infra_customer inside devopshero_app/ so it becomes part of the Django app
_INFRA_CUSTOMER_PATH = Path(__file__).parent.parent.parent.parent / "infra_customer"
if str(_INFRA_CUSTOMER_PATH) not in sys.path:
    sys.path.insert(0, str(_INFRA_CUSTOMER_PATH))

from . import app_config_builder
from . import deployment_executor
from . import deployment_worker

__all__ = [
    "app_config_builder",
    "deployment_executor",
    "deployment_worker",
]
