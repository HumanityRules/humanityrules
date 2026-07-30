"""Shared access control for cross-organization platform operator pages."""

import functools
from collections.abc import Callable

from django.http import Http404, HttpRequest, HttpResponse


PlatformView = Callable[..., HttpResponse]


def platform_staff_required(view_func: PlatformView) -> PlatformView:
    """Hide platform-wide operator pages from anyone who is not staff."""
    @functools.wraps(view_func)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not (request.user.is_authenticated and request.user.is_staff):
            raise Http404
        return view_func(request, *args, **kwargs)

    return wrapper
