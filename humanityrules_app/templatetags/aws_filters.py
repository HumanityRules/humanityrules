"""Template filters for AWS ARN formatting."""

from django import template

register = template.Library()


@register.filter
def arn_short_name(arn):
    """Extract the short resource name from an ARN (e.g. bucket name, table name)."""
    if not arn or not isinstance(arn, str):
        return arn
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return arn
    return parts[5] or arn
