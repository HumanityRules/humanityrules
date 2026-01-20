from django.apps import AppConfig
from django.conf import settings


class DevopsheroAppConfig(AppConfig):
    name = 'devopshero_app'

    def ready(self):
        """Start the deployment worker if enabled via settings."""
        if settings.DOH_RUN_DEPLOYMENT_WORKER:
            # Import here because deployment_worker imports models, which aren't ready at module load time
            from .services.deployment import deployment_worker
            deployment_worker.start_worker()
