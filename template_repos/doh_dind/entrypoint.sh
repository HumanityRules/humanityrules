#!/bin/sh
# doh-dind entrypoint: wraps docker:dind so we can run a snapshot-restore
# pass before signaling healthy, and hand off to the Python snapshotter as
# PID 1 (so SIGTERM from ECS reaches it directly).
#
# PID 1 progression:
#   1. /entrypoint.sh (this script)  — runs restore, then exec's snapshotter
#   2. snapshotter.py                — owns the live phase + SIGTERM handling;
#                                      when SIGTERM arrives it stops dockerd.
set -eu

DIND_HOST="tcp://127.0.0.1:2375"
PERSISTENCE_DIR="/var/lib/doh-dind/persistence"
LATEST_SNAPSHOT="$PERSISTENCE_DIR/latest.tar.zst"
READY_MARKER="/var/run/doh-restore-ready"

# The tag Hermes always launches. The restore step guarantees an image with this
# tag exists on the daemon before marking ready, so Hermes's first `docker run`
# never hits a pull.
TOOLBOX_TAG="doh-toolbox:latest"

if [ -z "${TOOL_IMAGE_BASE:-}" ]; then
    echo "[doh-dind] FATAL: TOOL_IMAGE_BASE not set" >&2
    exit 1
fi

#
# Start dockerd in the background via the upstream dind entrypoint. Passing
# `dockerd` as the first arg suppresses dockerd-entrypoint.sh's default
# --host=tcp://0.0.0.0:2375 (it only injects when the first arg starts with
# '-'), so our loopback bind wins and no external port is exposed.
#
DOCKERD_EXTRA_ARGS=""
# DOCKERD_EXTRA_ARGS="--debug"

echo "[doh-dind] Starting dockerd on $DIND_HOST $DOCKERD_EXTRA_ARGS"

# shellcheck disable=SC2086 # deliberate word-splitting for $DOCKERD_EXTRA_ARGS
/usr/local/bin/dockerd-entrypoint.sh dockerd --host="$DIND_HOST" $DOCKERD_EXTRA_ARGS &
DOCKERD_PID=$!

#
# Wait for dockerd to accept connections. The upstream image's own smoke test
# is `docker info`; we do the same with a timeout so a broken daemon fails
# loudly instead of hanging the task.
echo "[doh-dind] Waiting for dockerd to accept connections..."
i=0
until docker -H "$DIND_HOST" info >/dev/null 2>&1; do
    if [ "$i" -ge 60 ]; then
        echo "[doh-dind] FATAL: dockerd did not become reachable within 60s" >&2
        exit 1
    fi
    if ! kill -0 "$DOCKERD_PID" 2>/dev/null; then
        echo "[doh-dind] FATAL: dockerd exited before becoming reachable" >&2
        exit 1
    fi
    i=$((i + 1))
    sleep 1
done
echo "[doh-dind] dockerd is up (after ${i}s)"

export DOCKER_HOST="$DIND_HOST"

mkdir -p "$PERSISTENCE_DIR/snapshots"

# Snapshots are produced by `docker save`, which preserves image metadata
# (ENV, CMD, WORKDIR, etc.) — no --change flags needed on restore. The
# `docker load` preserves the tag baked into the tarball (TOOLBOX_TAG), so the
# restore path skips re-tagging. The snapshotter compacts the image before the
# layer chain gets deep.
RESTORE_STARTED_AT=$(date +%s)
if [ -s "$LATEST_SNAPSHOT" ] && zstd -dc "$LATEST_SNAPSHOT" | docker load; then
    RESTORE_DURATION=$(( $(date +%s) - RESTORE_STARTED_AT ))
    echo "[doh-dind] Restored $TOOLBOX_TAG from $LATEST_SNAPSHOT in ${RESTORE_DURATION}s"
else
    [ -s "$LATEST_SNAPSHOT" ] && echo "[doh-dind] ERROR: snapshot restore failed; falling back to base image pull" >&2
    echo "[doh-dind] Pulling base image $TOOL_IMAGE_BASE"
    if ! docker pull "$TOOL_IMAGE_BASE"; then
        echo "[doh-dind] FATAL: failed to pull $TOOL_IMAGE_BASE" >&2
        exit 1
    fi
    docker tag "$TOOL_IMAGE_BASE" "$TOOLBOX_TAG"
    RESTORE_DURATION=$(( $(date +%s) - RESTORE_STARTED_AT ))
    echo "[doh-dind] Tagged $TOOL_IMAGE_BASE as $TOOLBOX_TAG in ${RESTORE_DURATION}s"
fi

# Healthcheck in seed_app_templates.py greps for this file so Hermes (which
# depends_on: HEALTHY) only boots after restore-or-pull has finished. Without
# it, Hermes could race past us and hit `docker run` before the image exists.
touch "$READY_MARKER"
echo "[doh-dind] $READY_MARKER created — healthcheck will go green"

# Hand off PID 1 to the snapshotter. It inherits $DOCKERD_PID (via env) so it
# can kill dockerd during its SIGTERM handler.
export DOCKERD_PID TOOLBOX_TAG PERSISTENCE_DIR
echo "[doh-dind] exec'ing snapshotter.py (dockerd PID=$DOCKERD_PID)"
exec python3 /usr/local/bin/snapshotter.py
