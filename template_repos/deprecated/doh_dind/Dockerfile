FROM docker:26.1.0-dind

# Alpine-based; we need python3 for the snapshotter and zstd for the compressed
# tarball pipeline. py3-psutil isn't strictly required (our snapshotter uses
# the docker CLI, not the daemon socket directly), but keeping it in case we
# want in-process resource reporting later without a second image bump.
# bash + coreutils are for operator access via ECS Exec; not used by our own
# entrypoint or snapshotter.
RUN apk add --no-cache bash coreutils python3 py3-psutil zstd

# Disable stdio buffering so log lines hit the ECS log driver the moment
# they're emitted. Under a pipe, Python's default is block-buffered — which
# means a logger.info can sit invisible in memory for many seconds (or
# forever, if we crash before flush) and look to an operator like the line
# was never emitted.
ENV PYTHONUNBUFFERED=1

COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 snapshotter.py /usr/local/bin/snapshotter.py

ENTRYPOINT ["/entrypoint.sh"]
