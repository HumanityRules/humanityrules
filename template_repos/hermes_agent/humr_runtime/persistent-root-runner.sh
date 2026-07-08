#!/bin/bash
set -euo pipefail

: "${HERMES_PERSISTENT_ROOT:?HERMES_PERSISTENT_ROOT must be set}"
: "${HERMES_CHECKPOINT_ROOT:?HERMES_CHECKPOINT_ROOT must be set}"
CHECKPOINT_ARCHIVE_NAME="rootfs.tar.zst"
RUNTIME_PID=""
TERMINATION_REQUESTED=0
SIGTERM_MS=""
IMAGE_OWNED_DIRS=(
    /opt/humr
    /opt/hermes
)

die() {
    echo "FATAL: $*" >&2
    exit 1
}

now_ms() {
    date +%s%3N
}

format_duration_ms() {
    local duration_ms="$1"

    printf "%d.%03ds" "$((duration_ms / 1000))" "$((duration_ms % 1000))"
}

format_size_bytes() {
    local bytes="$1"

    printf "%d MiB (%d bytes)" "$((bytes / 1024 / 1024))" "$bytes"
}

persistent_root_initialized() {
    [ -f "${HERMES_PERSISTENT_ROOT}/.humr-hermes-persistent-root" ]
}

checkpoint_archive_path() {
    printf "%s/%s" "$HERMES_CHECKPOINT_ROOT" "$CHECKPOINT_ARCHIVE_NAME"
}

checkpoint_root_mounted() {
    [ -d "$HERMES_CHECKPOINT_ROOT" ] && mountpoint -q "$HERMES_CHECKPOINT_ROOT"
}

copy_runtime_file() {
    local path="$1"

    if [ -e "$path" ]; then
        mkdir -p "${HERMES_PERSISTENT_ROOT}$(dirname "$path")"
        rm -f "${HERMES_PERSISTENT_ROOT}${path}"
        cp -L "$path" "${HERMES_PERSISTENT_ROOT}${path}"
    fi
}

prepare_runtime_filesystem() {
    mkdir -p \
        "${HERMES_PERSISTENT_ROOT}/dev" \
        "${HERMES_PERSISTENT_ROOT}/proc" \
        "${HERMES_PERSISTENT_ROOT}/run" \
        "${HERMES_PERSISTENT_ROOT}/sys" \
        "${HERMES_PERSISTENT_ROOT}/tmp"
    chmod 1777 "${HERMES_PERSISTENT_ROOT}/tmp"

    mountpoint -q "${HERMES_PERSISTENT_ROOT}/proc" \
        || mount -t proc proc "${HERMES_PERSISTENT_ROOT}/proc" \
        || die "failed to mount proc into ${HERMES_PERSISTENT_ROOT}; persistent-root runner needs CAP_SYS_ADMIN"
    mountpoint -q "${HERMES_PERSISTENT_ROOT}/dev" \
        || mount --rbind /dev "${HERMES_PERSISTENT_ROOT}/dev" \
        || die "failed to mount dev into ${HERMES_PERSISTENT_ROOT}; persistent-root runner needs CAP_SYS_ADMIN"
    mount --make-rslave "${HERMES_PERSISTENT_ROOT}/dev" \
        || die "failed to set ${HERMES_PERSISTENT_ROOT}/dev mount propagation"
    mountpoint -q "${HERMES_PERSISTENT_ROOT}/sys" \
        || mount --rbind /sys "${HERMES_PERSISTENT_ROOT}/sys" \
        || die "failed to mount sys into ${HERMES_PERSISTENT_ROOT}; persistent-root runner needs CAP_SYS_ADMIN"
    mount --make-rslave "${HERMES_PERSISTENT_ROOT}/sys" \
        || die "failed to set ${HERMES_PERSISTENT_ROOT}/sys mount propagation"
    mountpoint -q "${HERMES_PERSISTENT_ROOT}/run" \
        || mount -t tmpfs tmpfs "${HERMES_PERSISTENT_ROOT}/run" \
        || die "failed to mount run tmpfs into ${HERMES_PERSISTENT_ROOT}; persistent-root runner needs CAP_SYS_ADMIN"

    copy_runtime_file /etc/resolv.conf
    copy_runtime_file /etc/hosts
    copy_runtime_file /etc/hostname
    # HUMR-owned login-shell PATH drop-in. Without refreshing this on every
    # boot, edits to /etc/profile.d/humr-bin.sh in the image (e.g. PATH
    # additions) would never reach existing persistent roots — the file is
    # outside IMAGE_OWNED_DIRS by design (we don't want to clobber the rest
    # of /etc).
    copy_runtime_file /etc/profile.d/humr-bin.sh
}

sync_image_owned_dirs() {
    local start_ms
    local end_ms
    local duration_ms
    local path

    start_ms="$(now_ms)"

    for path in "${IMAGE_OWNED_DIRS[@]}"; do
        if [ ! -d "$path" ]; then
            continue
        fi
        mkdir -p "${HERMES_PERSISTENT_ROOT}${path}"
        rsync -aH --delete --numeric-ids --one-file-system \
            "${path}/" "${HERMES_PERSISTENT_ROOT}${path}/"
    done

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Image-owned sync complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms)."
}

initialize_persistent_root() {
    local root_exclude="${HERMES_PERSISTENT_ROOT%/}"
    local checkpoint_exclude="${HERMES_CHECKPOINT_ROOT%/}"
    local start_ms
    local end_ms
    local duration_ms

    start_ms="$(now_ms)"

    echo "[persistent-root] Initializing ${HERMES_PERSISTENT_ROOT} from image root..."
    rsync -aH --numeric-ids --one-file-system \
        --exclude="${root_exclude}/***" \
        --exclude="${checkpoint_exclude}/***" \
        --exclude="/dev/***" \
        --exclude="/opt/humr/***" \
        --exclude="/opt/hermes/***" \
        --exclude="/proc/***" \
        --exclude="/run/***" \
        --exclude="/sys/***" \
        --exclude="/tmp/***" \
        / "${HERMES_PERSISTENT_ROOT}/"
    prepare_runtime_filesystem
    touch "${HERMES_PERSISTENT_ROOT}/.humr-hermes-persistent-root"

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Initialization complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms)."
}

restore_persistent_root_from_checkpoint() {
    local archive
    local start_ms
    local end_ms
    local duration_ms

    if ! checkpoint_root_mounted; then
        echo "[persistent-root] Checkpoint root ${HERMES_CHECKPOINT_ROOT} is not mounted; no restore source."
        return 1
    fi

    archive="$(checkpoint_archive_path)"
    if [ ! -s "$archive" ]; then
        echo "[persistent-root] No checkpoint archive found at ${archive}."
        return 1
    fi

    start_ms="$(now_ms)"
    echo "[persistent-root] Restoring ${HERMES_PERSISTENT_ROOT} from ${archive} ($(format_size_bytes "$(stat -c %s "$archive")"))..."
    tar --extract \
        --zstd \
        --file "$archive" \
        --directory "$HERMES_PERSISTENT_ROOT" \
        --numeric-owner \
        --same-owner \
        --xattrs \
        --acls \
        || die "failed to restore persistent root from ${archive}"
    prepare_runtime_filesystem
    touch "${HERMES_PERSISTENT_ROOT}/.humr-hermes-persistent-root"

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Restore complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms)."
    return 0
}

reuse_persistent_root() {
    local start_ms
    local end_ms
    local duration_ms

    start_ms="$(now_ms)"

    echo "[persistent-root] Reusing existing ${HERMES_PERSISTENT_ROOT}."
    prepare_runtime_filesystem

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Reuse preparation complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms)."
}

checkpoint_persistent_root() {
    local archive
    local tmp_archive
    local start_ms
    local end_ms
    local duration_ms

    if ! checkpoint_root_mounted; then
        echo "[persistent-root] Checkpoint root ${HERMES_CHECKPOINT_ROOT} is not mounted; skipping checkpoint."
        return
    fi

    archive="$(checkpoint_archive_path)"
    tmp_archive="${archive}.tmp.$$"
    rm -f "$tmp_archive"

    start_ms="$(now_ms)"
    echo "[persistent-root] Writing checkpoint to ${archive}..."
    sync
    # zstd: GNU tar shells out to the `zstd` binary on PATH (apt-installed).
    # Default level 3, single-threaded — CPU is rarely the bottleneck here
    # since EFS write throughput (~50 MB/s) caps the tar pipeline well below
    # zstd's ~400 MB/s/core. If CPU ever becomes the limit, add `-T0` via
    # ZSTD_NBTHREADS or pipe through `zstd -T0` explicitly.
    tar --create \
        --zstd \
        --file "$tmp_archive" \
        --directory "$HERMES_PERSISTENT_ROOT" \
        --one-file-system \
        --numeric-owner \
        --xattrs \
        --acls \
        --exclude="./dev" \
        --exclude="./proc" \
        --exclude="./run" \
        --exclude="./sys" \
        --exclude="./tmp" \
        --exclude="./opt/humr" \
        --exclude="./opt/hermes" \
        . \
        || die "failed to write checkpoint archive ${tmp_archive}"
    sync
    mv -f "$tmp_archive" "$archive"
    sync

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Checkpoint complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms); archive size $(format_size_bytes "$(stat -c %s "$archive")")."
}

# A leftover tmp archive means a previous shutdown was SIGKILLed mid-checkpoint
# (its state was lost; the surviving rootfs.tar.zst is one generation older).
# Without this sweep they also accumulate on EFS forever, since the checkpoint
# path only removes the tmp file of the current PID.
report_stale_checkpoint_tmp_files() {
    local stale

    if ! checkpoint_root_mounted; then
        return
    fi
    for stale in "$(checkpoint_archive_path)".tmp.*; do
        if [ ! -e "$stale" ]; then
            continue
        fi
        echo "[persistent-root] WARNING: stale checkpoint temp file ${stale} ($(format_size_bytes "$(stat -c %s "$stale")")) — a previous shutdown was likely killed mid-checkpoint. Removing it."
        rm -f "$stale"
    done
}

request_termination() {
    if [ "$TERMINATION_REQUESTED" -eq 1 ]; then
        return
    fi

    TERMINATION_REQUESTED=1
    SIGTERM_MS="$(now_ms)"
    echo "[persistent-root] Termination requested; forwarding SIGTERM to runtime."
    if [ -n "$RUNTIME_PID" ] && kill -0 "$RUNTIME_PID" 2>/dev/null; then
        kill -TERM "-$RUNTIME_PID" 2>/dev/null \
            || kill -TERM "$RUNTIME_PID" 2>/dev/null \
            || true
    fi
}

main() {
    local exit_code
    local shutdown_ms

    if [ "$#" -eq 0 ]; then
        die "no command supplied"
    fi
    if [[ "$HERMES_PERSISTENT_ROOT" != /* ]]; then
        die "HERMES_PERSISTENT_ROOT must be an absolute path"
    fi
    if [ "$HERMES_PERSISTENT_ROOT" = "/" ]; then
        die "HERMES_PERSISTENT_ROOT must not be /"
    fi
    if [[ "$HERMES_CHECKPOINT_ROOT" != /* ]]; then
        die "HERMES_CHECKPOINT_ROOT must be an absolute path"
    fi
    if [ "$HERMES_CHECKPOINT_ROOT" = "/" ]; then
        die "HERMES_CHECKPOINT_ROOT must not be /"
    fi

    mkdir -p "$HERMES_PERSISTENT_ROOT"
    report_stale_checkpoint_tmp_files
    if persistent_root_initialized; then
        reuse_persistent_root
    else
        restore_persistent_root_from_checkpoint || initialize_persistent_root
    fi

    sync_image_owned_dirs

    trap request_termination TERM INT
    chroot "$HERMES_PERSISTENT_ROOT" \
        /usr/bin/setsid \
        /usr/bin/setpriv --bounding-set=-sys_admin --inh-caps=-all --ambient-caps=-all \
        /usr/bin/env HOME=/root USER=root LOGNAME=root "$@" &
    RUNTIME_PID=$!

    set +e
    wait "$RUNTIME_PID"
    exit_code=$?
    if [ "$TERMINATION_REQUESTED" -eq 1 ] && kill -0 "$RUNTIME_PID" 2>/dev/null; then
        wait "$RUNTIME_PID"
        exit_code=$?
    fi
    set -e

    if [ "$TERMINATION_REQUESTED" -eq 1 ]; then
        shutdown_ms="$(($(now_ms) - SIGTERM_MS))"
        echo "[persistent-root] Runtime exited with code ${exit_code} in $(format_duration_ms "$shutdown_ms") (${shutdown_ms} ms) after SIGTERM."
        checkpoint_persistent_root
    else
        echo "[persistent-root] Runtime exited on its own with code ${exit_code}; skipping checkpoint."
    fi

    exit "$exit_code"
}

main "$@"
