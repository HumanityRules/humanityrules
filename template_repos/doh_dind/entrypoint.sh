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

# Tag Hermes always launches. The restore step guarantees an image with this
# tag exists on the daemon before marking ready, so Hermes's first `docker run`
# never hits a pull.
TOOLBOX_TAG="doh-toolbox:latest"

if [ -z "${TOOL_IMAGE_BASE:-}" ]; then
    echo "[doh-dind] FATAL: TOOL_IMAGE_BASE not set" >&2
    exit 1
fi

RESTORE_MODE="${DOH_SNAPSHOT_RESTORE:-auto}"
case "$RESTORE_MODE" in
    auto|skip|force_rebuild) ;;
    *)
        echo "[doh-dind] FATAL: DOH_SNAPSHOT_RESTORE='$RESTORE_MODE' (expected auto|skip|force_rebuild)" >&2
        exit 1
        ;;
esac

# Start dockerd in the background via the upstream dind entrypoint. Passing
# `dockerd` as the first arg suppresses dockerd-entrypoint.sh's default
# --host=tcp://0.0.0.0:2375 (it only injects when the first arg starts with
# '-'), so our loopback bind wins and no external port is exposed.
echo "[doh-dind] Starting dockerd on $DIND_HOST"
/usr/local/bin/dockerd-entrypoint.sh dockerd --host="$DIND_HOST" &
DOCKERD_PID=$!

# Wait for dockerd to accept connections. The upstream image's own smoke test
# is `docker info`; we do the same with a timeout so a broken daemon fails
# loudly instead of hanging the task.
echo "[doh-dind] Waiting for dockerd to accept connections..."
for i in $(seq 1 60); do
    if docker -H "$DIND_HOST" info >/dev/null 2>&1; then
        echo "[doh-dind] dockerd is up (after ${i}s)"
        break
    fi
    if ! kill -0 "$DOCKERD_PID" 2>/dev/null; then
        echo "[doh-dind] FATAL: dockerd exited before becoming reachable" >&2
        exit 1
    fi
    sleep 1
done
if ! docker -H "$DIND_HOST" info >/dev/null 2>&1; then
    echo "[doh-dind] FATAL: dockerd did not become reachable within 60s" >&2
    exit 1
fi

export DOCKER_HOST="$DIND_HOST"

mkdir -p "$PERSISTENCE_DIR" "$PERSISTENCE_DIR/snapshots"

# force_rebuild: wipe latest.tar.zst so the import path below falls through
# to the clean pull. Keeps /snapshots history around for manual recovery.
if [ "$RESTORE_MODE" = "force_rebuild" ] && [ -f "$LATEST_SNAPSHOT" ]; then
    echo "[doh-dind] DOH_SNAPSHOT_RESTORE=force_rebuild — removing $LATEST_SNAPSHOT"
    rm -f "$LATEST_SNAPSHOT" "$PERSISTENCE_DIR/latest.meta.json"
fi

# ENV vars the base image (nikolaik/python-nodejs) sets at build time. `docker
# export` drops the image config, so we re-apply the load-bearing ones via
# `docker import --change` to keep the restored image behaviourally close to
# a freshly-pulled base:
#   PATH        — harmless if redundant with /etc/profile, load-bearing if
#                 anything sets up an override (poetry, conda)
#   LANG        — Python stdout encoding and locale
#   POETRY_HOME — poetry is pre-installed in the base; without this it falls
#                 back to its own default and state splits
# GPG_KEY / PYTHON_VERSION / PYTHON_SHA256 are build-time only, skipped.
IMPORT_CHANGES="--change=ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin --change=ENV LANG=C.UTF-8 --change=ENV POETRY_HOME=/usr/local"

restored_from_snapshot=0
if [ "$RESTORE_MODE" != "skip" ] && [ -s "$LATEST_SNAPSHOT" ]; then
    echo "[doh-dind] Restoring tool image from $LATEST_SNAPSHOT"
    # shellcheck disable=SC2086 # IMPORTANT: deliberate word-splitting for --change flags
    if zstd -dc "$LATEST_SNAPSHOT" | docker import $IMPORT_CHANGES - "$TOOLBOX_TAG"; then
        echo "[doh-dind] Restored $TOOLBOX_TAG from snapshot"
        restored_from_snapshot=1
    else
        echo "[doh-dind] WARN: restore failed; falling back to base image pull" >&2
    fi
fi

if [ "$restored_from_snapshot" -eq 0 ]; then
    echo "[doh-dind] Pulling base image $TOOL_IMAGE_BASE"
    if ! docker pull "$TOOL_IMAGE_BASE"; then
        echo "[doh-dind] FATAL: failed to pull $TOOL_IMAGE_BASE" >&2
        exit 1
    fi
    docker tag "$TOOL_IMAGE_BASE" "$TOOLBOX_TAG"
    echo "[doh-dind] Tagged $TOOL_IMAGE_BASE as $TOOLBOX_TAG"
fi

# Healthcheck in seed_app_templates.py greps for this file so Hermes (which
# depends_on: HEALTHY) only boots after restore-or-pull has finished. Without
# it, Hermes could race past us and hit `docker run` before the image exists.
touch "$READY_MARKER"
echo "[doh-dind] $READY_MARKER created — healthcheck will go green"

# Hand off PID 1 to the snapshotter. It inherits $DOCKERD_PID (via env) so it
# can kill dockerd during its SIGTERM handler.
export DOCKERD_PID
export TOOLBOX_TAG
export PERSISTENCE_DIR
echo "[doh-dind] exec'ing snapshotter.py (dockerd PID=$DOCKERD_PID)"
exec python3 /usr/local/bin/snapshotter.py
