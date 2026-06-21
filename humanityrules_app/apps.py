import sys

from django.apps import AppConfig
from django.conf import settings


def _is_management_command() -> bool:
    """Detect if running as a Django management command (excluding runserver)."""
    if sys.argv and sys.argv[0].endswith('manage.py'):
        # Allow runserver to start the worker for local development
        if len(sys.argv) >= 2 and sys.argv[1] == 'runserver':
            return False
        return True
    return False


class HumanityrulesAppConfig(AppConfig):
    name = 'humanityrules_app'

    def ready(self):
        """Initialize services on app startup."""
        self._init_posthog()
        self._init_posthog_logs()
        self._init_job_worker()

    def _init_posthog(self):
        """Initialize PostHog analytics with exception autocapture. Disabled in DEBUG mode."""
        posthog_key = getattr(settings, 'POSTHOG_API_KEY', None)
        if posthog_key and not settings.DEBUG:
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

    def _init_posthog_logs(self):
        """Ship Python logs to PostHog via OpenTelemetry. Disabled in DEBUG mode."""
        posthog_key = getattr(settings, 'POSTHOG_API_KEY', None)
        if not posthog_key or settings.DEBUG:
            return

        import logging
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        logger_provider = LoggerProvider()
        set_logger_provider(logger_provider)

        otlp_exporter = OTLPLogExporter(
            endpoint="https://us.i.posthog.com/i/v1/logs",
            headers={"Authorization": f"Bearer {posthog_key}"},
        )
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_exporter))

        otel_handler = LoggingHandler(logger_provider=logger_provider)
        logging.getLogger("humanityrules_app").addHandler(otel_handler)

    def _init_job_worker(self):
        """Start the job worker for web server processes only."""
        if not settings.DOH_RUN_JOB_WORKER:
            return

        if _is_management_command():
            return

        # Import here because job_worker imports models, which aren't ready at module load time
        from .services.jobs import job_worker
        # Web-server-embedded worker is always unscoped — it serves real UI traffic.
        job_worker.start_worker(label="")
