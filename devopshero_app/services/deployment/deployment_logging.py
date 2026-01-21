"""Deployment log handler utilities."""

import asyncio
import logging
import traceback
from datetime import datetime
from pathlib import Path
import uuid

from asgiref.sync import sync_to_async

from devopshero_app import models


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


def _map_level(levelno: int) -> str:
    if levelno >= logging.WARNING:
        return models.DeploymentLog.Level.ERROR
    if levelno >= logging.INFO:
        return models.DeploymentLog.Level.INFO
    if levelno >= logging.DEBUG:
        return models.DeploymentLog.Level.DEBUG
    return models.DeploymentLog.Level.INFO


class DeploymentLogHandler(logging.Handler):
    """Persist log records to DeploymentLog."""

    def __init__(self, deployment: models.Deployment, source_default: str) -> None:
        super().__init__(level=logging.NOTSET)
        self._deployment = deployment
        self._source_default = source_default
        self.addFilter(logging.Filter("devopshero_app"))

    def _create_log(self, source: str, level: str, message: str, details: dict[str, object]) -> None:
        """Sync DB write - called directly or via sync_to_async."""
        models.DeploymentLog.objects.create(
            deployment=self._deployment,
            source=source,
            level=level,
            message=message,
            details=details,
        )

    def emit(self, record: logging.LogRecord) -> None:
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

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                # Async context: offload sync DB call to thread pool
                coro = sync_to_async(self._create_log, thread_sensitive=False)(
                    source=source,
                    level=_map_level(record.levelno),
                    message=rendered_message,
                    details=details,
                )
                asyncio.ensure_future(coro)
            else:
                # Sync context: call directly
                self._create_log(
                    source=source,
                    level=_map_level(record.levelno),
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
        self._previous_level = self._logger.level
        if self._logger.level == logging.NOTSET or self._logger.level > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._logger.removeHandler(self._handler)
        if self._previous_level is not None:
            self._logger.setLevel(self._previous_level)
        return False
