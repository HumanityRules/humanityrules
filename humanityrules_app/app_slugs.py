"""Derive and validate app hostname labels. User-facing copy says "agent"; code says "app"."""

import re

from django.core.exceptions import ValidationError
from django.utils.text import slugify


APP_HOSTNAME_LABEL_ERROR = "Agent hostname labels must contain lowercase letters and digits only."


def derive_app_slug(value: str) -> str:
    """Normalize free text into a dashless app slug."""
    slug = slugify(value=value, allow_unicode=False)
    return re.sub(pattern=r"[^a-z0-9]", repl="", string=slug)


def validate_app_hostname_label(value: str) -> None:
    """Validate a stored app hostname label."""
    if re.fullmatch(pattern=r"[a-z0-9]+", string=value) is None:
        raise ValidationError(message=APP_HOSTNAME_LABEL_ERROR, code="invalid_app_hostname_label")


def require_valid_app_hostname_label(value: str) -> None:
    """Raise ValueError when a runtime input is not a valid app hostname label."""
    try:
        validate_app_hostname_label(value=value)
    except ValidationError as exc:
        raise ValueError(APP_HOSTNAME_LABEL_ERROR) from exc
