FROM docker:26.1.0-dind

# Alpine-based; we need python3 for the snapshotter and zstd for the compressed
# tarball pipeline. py3-psutil isn't strictly required (our snapshotter uses
# the docker CLI, not the daemon socket directly), but keeping it in case we
# want in-process resource reporting later without a second image bump.
RUN apk add --no-cache python3 py3-psutil zstd

COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 snapshotter.py /usr/local/bin/snapshotter.py

ENTRYPOINT ["/entrypoint.sh"]
