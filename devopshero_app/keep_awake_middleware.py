"""Keep the job worker actively polling while authenticated users are around.

The control-plane Aurora cluster is configured with `min_capacity=0` and
auto-pause, so it pauses after a few minutes of zero connections (saving the
0.5-ACU floor cost). The job worker would normally defeat that by polling the
DB every second forever; instead, it now sleeps when idle and only enters
1-second active polling while this middleware says a user is active.

Every authenticated request extends a sliding window (`KEEP_AWAKE_SECONDS`).
While the window is open, the worker polls aggressively so deployments,
provisioning, and teardowns are picked up immediately. After the window expires
with no further authenticated traffic, the worker goes quiet, the cluster
auto-pauses, and we stop paying for compute until the next user shows up
(at the cost of a one-time ~15 s resume on the first request).
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from devopshero_app.services.jobs import job_worker

KEEP_AWAKE_SECONDS = 4 * 60 * 60


class KeepWorkerAwakeMiddleware:
    """Extend the worker's active-polling window on every authenticated request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            job_worker.keep_awake_for(seconds=KEEP_AWAKE_SECONDS)
        return self.get_response(request)
