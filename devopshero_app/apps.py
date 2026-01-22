from django.apps import AppConfig
from django.conf import settings


class DevopsheroAppConfig(AppConfig):
    name = 'devopshero_app'

    def ready(self):
        """Start the job worker if enabled via settings."""
        if settings.DOH_RUN_JOB_WORKER:
            # Import here because job_worker imports models, which aren't ready at module load time
            from .services.deployment import job_worker
            job_worker.start_worker()
