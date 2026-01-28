from django.apps import AppConfig
from django.conf import settings


class DevopsheroAppConfig(AppConfig):
    name = 'devopshero_app'

    def ready(self):
        """Initialize services on app startup."""
        self._init_posthog()
        self._init_job_worker()

    def _init_posthog(self):
        """Initialize PostHog analytics with exception autocapture."""
        posthog_key = getattr(settings, 'POSTHOG_API_KEY', None)
        if posthog_key:
            from posthog import Posthog
            import posthog as posthog_module

            # Use reverse proxy if configured (bypasses ad blockers)
            proxy_host = getattr(settings, 'POSTHOG_PROXY_HOST', None)
            host = proxy_host if proxy_host else getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com')

            client = Posthog(
                posthog_key,
                host=host,
                enable_exception_autocapture=True,
            )
            posthog_module.default_client = client

    def _init_job_worker(self):
        """Start the job worker if enabled via settings."""
        if settings.DOH_RUN_JOB_WORKER:
            # Import here because job_worker imports models, which aren't ready at module load time
            from .services.deployment import job_worker
            job_worker.start_worker()
