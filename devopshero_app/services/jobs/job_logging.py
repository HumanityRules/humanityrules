"""Job log handler utilities for deployments and environment provisioning."""

import logging
import threading
import traceback
from datetime import datetime
from pathlib import Path
import uuid

from devopshero_app import models


# Thread-local storage for tracking active job contexts.
# When multiple jobs run concurrently, each handler should only capture logs
# from its own job context, not from other concurrent jobs.
_job_context = threading.local()


def _normalize_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (Path, uuid.UUID)):
        return str(value)
    if isinstance(value, BaseException):
        return str(value)
    return str(value)


def _extract_params(args: object) -> dict[str, object]:
    if isinstance(args, dict):
        return args
    if isinstance(args, tuple) and len(args) == 0:
        return {}
    return {"_args": args}


def _map_deployment_level(levelno: int) -> str:
    if levelno >= logging.WARNING:
        return models.DeploymentLog.Level.ERROR
    if levelno >= logging.INFO:
        return models.DeploymentLog.Level.INFO
    if levelno >= logging.DEBUG:
        return models.DeploymentLog.Level.DEBUG
    return models.DeploymentLog.Level.INFO


def _map_environment_level(levelno: int) -> str:
    if levelno >= logging.WARNING:
        return models.EnvironmentLog.Level.ERROR
    if levelno >= logging.INFO:
        return models.EnvironmentLog.Level.INFO
    if levelno >= logging.DEBUG:
        return models.EnvironmentLog.Level.DEBUG
    return models.EnvironmentLog.Level.INFO


# Prefixes for loggers that should be captured by job log handlers.
# This excludes async code paths (views, agent) that would cause SynchronousOnlyOperation errors.
_JOB_LOG_PREFIXES = (
    "devopshero_app.services.jobs",
    "devopshero_app.services.infra_customer",
)


class _JobLogFilter(logging.Filter):
    """Filter that only allows logs from job-related modules (sync code paths)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(_JOB_LOG_PREFIXES)


class DeploymentLogHandler(logging.Handler):
    """Persist log records to DeploymentLog."""

    def __init__(self, deployment: models.Deployment, source_default: str) -> None:
        super().__init__(level=logging.NOTSET)
        self._deployment = deployment
        self._deployment_id = deployment.id
        self._source_default = source_default
        self.addFilter(_JobLogFilter())

    def emit(self, record: logging.LogRecord) -> None:
        # Only emit if this log comes from our job context (prevents cross-talk
        # between concurrent jobs that share the same root logger)
        current_deployment_id = getattr(_job_context, "deployment_id", None)
        if current_deployment_id != self._deployment_id:
            return

        try:
            source = record.__dict__.get("source") or self._source_default
            params = _normalize_value(_extract_params(record.args))
            template = str(record.msg)
            try:
                rendered_message = record.getMessage()
            except TypeError as exc:
                rendered_message = template
                params = {
                    "format_error": str(exc),
                    "args": _normalize_value(record.args),
                }

            details: dict[str, object] = {
                "template": template,
                "params": params,
                "logger": record.name,
            }

            stream = record.__dict__.get("stream")
            if stream is not None:
                details["stream"] = stream

            if record.exc_info:
                details["traceback"] = "".join(traceback.format_exception(*record.exc_info))

            models.DeploymentLog.objects.create(
                deployment=self._deployment,
                source=source,
                level=_map_deployment_level(record.levelno),
                message=rendered_message,
                details=details,
            )
        except Exception:
            self.handleError(record)


class EnvironmentLogHandler(logging.Handler):
    """Persist log records to EnvironmentLog."""

    def __init__(self, environment: models.Environment, source_default: str) -> None:
        super().__init__(level=logging.NOTSET)
        self._environment = environment
        self._environment_id = environment.id
        self._source_default = source_default
        self.addFilter(_JobLogFilter())

    def emit(self, record: logging.LogRecord) -> None:
        # Only emit if this log comes from our job context (prevents cross-talk
        # between concurrent jobs that share the same root logger)
        current_environment_id = getattr(_job_context, "environment_id", None)
        if current_environment_id != self._environment_id:
            return

        try:
            source = record.__dict__.get("source") or self._source_default
            params = _normalize_value(_extract_params(record.args))
            template = str(record.msg)
            try:
                rendered_message = record.getMessage()
            except TypeError as exc:
                rendered_message = template
                params = {
                    "format_error": str(exc),
                    "args": _normalize_value(record.args),
                }

            details: dict[str, object] = {
                "template": template,
                "params": params,
                "logger": record.name,
            }

            stream = record.__dict__.get("stream")
            if stream is not None:
                details["stream"] = stream

            if record.exc_info:
                details["traceback"] = "".join(traceback.format_exception(*record.exc_info))

            models.EnvironmentLog.objects.create(
                environment=self._environment,
                source=source,
                level=_map_environment_level(record.levelno),
                message=rendered_message,
                details=details,
            )
        except Exception:
            self.handleError(record)


class DeploymentLogContext:
    """Attach a DeploymentLogHandler for the duration of a deployment."""

    def __init__(self, deployment: models.Deployment, source_default: str) -> None:
        self._deployment = deployment
        self._source_default = source_default
        self._handler = DeploymentLogHandler(deployment=self._deployment, source_default=self._source_default)
        self._logger = logging.getLogger("devopshero_app")
        self._previous_level: int | None = None

    def __enter__(self):
        # Set thread-local context so handler knows which logs belong to this job
        _job_context.deployment_id = self._deployment.id
        self._previous_level = self._logger.level
        if self._logger.level == logging.NOTSET or self._logger.level > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._logger.removeHandler(self._handler)
        # Clear thread-local context
        _job_context.deployment_id = None
        if self._previous_level is not None:
            self._logger.setLevel(self._previous_level)
        return False


class EnvironmentLogContext:
    """Attach an EnvironmentLogHandler for the duration of environment provisioning."""

    def __init__(self, environment: models.Environment, source_default: str) -> None:
        self._environment = environment
        self._source_default = source_default
        self._handler = EnvironmentLogHandler(environment=self._environment, source_default=self._source_default)
        self._logger = logging.getLogger("devopshero_app")
        self._previous_level: int | None = None

    def __enter__(self):
        # Set thread-local context so handler knows which logs belong to this job
        _job_context.environment_id = self._environment.id
        self._previous_level = self._logger.level
        if self._logger.level == logging.NOTSET or self._logger.level > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._logger.removeHandler(self._handler)
        # Clear thread-local context
        _job_context.environment_id = None
        if self._previous_level is not None:
            self._logger.setLevel(self._previous_level)
        return False
