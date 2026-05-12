#!/bin/bash
set -euo pipefail

HERMES_PERSISTENT_ROOT="${HERMES_PERSISTENT_ROOT:-/hermes-persistent-root}"
HERMES_CHECKPOINT_ROOT="${HERMES_CHECKPOINT_ROOT:-/hermes-checkpoint}"
CHECKPOINT_ARCHIVE_NAME="rootfs.tar"
RUNTIME_PID=""
TERMINATION_REQUESTED=0
IMAGE_OWNED_DIRS=(
    /opt/doh
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

is_empty_dir() {
    [ -z "$(find "$HERMES_PERSISTENT_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]
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
}

install_passwordless_sudo() {
    mkdir -p "${HERMES_PERSISTENT_ROOT}/etc/sudoers.d"
    cat > "${HERMES_PERSISTENT_ROOT}/etc/sudoers.d/hermeswebui" <<'EOF'
hermeswebui ALL=(root) NOPASSWD:ALL
EOF
    chmod 0440 "${HERMES_PERSISTENT_ROOT}/etc/sudoers.d/hermeswebui"
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
        --exclude="/opt/doh/***" \
        --exclude="/opt/hermes/***" \
        --exclude="/proc/***" \
        --exclude="/run/***" \
        --exclude="/sys/***" \
        --exclude="/tmp/***" \
        / "${HERMES_PERSISTENT_ROOT}/"
    prepare_runtime_filesystem
    install_passwordless_sudo
    touch "${HERMES_PERSISTENT_ROOT}/.doh-hermes-persistent-root"

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
    echo "[persistent-root] Restoring ${HERMES_PERSISTENT_ROOT} from ${archive}..."
    tar --extract \
        --file "$archive" \
        --directory "$HERMES_PERSISTENT_ROOT" \
        --numeric-owner \
        --same-owner \
        --xattrs \
        --acls \
        || die "failed to restore persistent root from ${archive}"
    prepare_runtime_filesystem
    install_passwordless_sudo
    touch "${HERMES_PERSISTENT_ROOT}/.doh-hermes-persistent-root"

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
    install_passwordless_sudo

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
    tar --create \
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
        --exclude="./opt/doh" \
        --exclude="./opt/hermes" \
        . \
        || die "failed to write checkpoint archive ${tmp_archive}"
    sync
    mv -f "$tmp_archive" "$archive"
    sync

    end_ms="$(now_ms)"
    duration_ms="$((end_ms - start_ms))"
    echo "[persistent-root] Checkpoint complete in $(format_duration_ms "$duration_ms") (${duration_ms} ms)."
}

request_termination() {
    if [ "$TERMINATION_REQUESTED" -eq 1 ]; then
        return
    fi

    TERMINATION_REQUESTED=1
    echo "[persistent-root] Termination requested; forwarding SIGTERM to runtime."
    if [ -n "$RUNTIME_PID" ] && kill -0 "$RUNTIME_PID" 2>/dev/null; then
        kill -TERM "-$RUNTIME_PID" 2>/dev/null \
            || kill -TERM "$RUNTIME_PID" 2>/dev/null \
            || true
    fi
}

main() {
    local exit_code

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
    if is_empty_dir; then
        restore_persistent_root_from_checkpoint || initialize_persistent_root
    else
        reuse_persistent_root
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
        checkpoint_persistent_root
    fi

    exit "$exit_code"
}

main "$@"
