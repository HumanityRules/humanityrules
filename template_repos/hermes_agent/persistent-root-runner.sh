#!/bin/bash
set -euo pipefail

HERMES_PERSISTENT_ROOT="${HERMES_PERSISTENT_ROOT:-/hermes-persistent-root}"

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

initialize_persistent_root() {
    local root_exclude="${HERMES_PERSISTENT_ROOT%/}"
    local start_ms
    local end_ms
    local duration_ms

    start_ms="$(now_ms)"

    echo "[persistent-root] Initializing ${HERMES_PERSISTENT_ROOT} from image root..."
    rsync -aH --numeric-ids --one-file-system \
        --exclude="${root_exclude}/***" \
        --exclude="/dev/***" \
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

main() {
    if [ "$#" -eq 0 ]; then
        die "no command supplied"
    fi
    if [[ "$HERMES_PERSISTENT_ROOT" != /* ]]; then
        die "HERMES_PERSISTENT_ROOT must be an absolute path"
    fi
    if [ "$HERMES_PERSISTENT_ROOT" = "/" ]; then
        die "HERMES_PERSISTENT_ROOT must not be /"
    fi

    mkdir -p "$HERMES_PERSISTENT_ROOT"
    if is_empty_dir; then
        initialize_persistent_root
    else
        reuse_persistent_root
    fi

    exec chroot "$HERMES_PERSISTENT_ROOT" \
        /usr/bin/setpriv --bounding-set=-sys_admin --inh-caps=-all --ambient-caps=-all \
        /usr/bin/env HOME=/root USER=root LOGNAME=root "$@"
}

main "$@"
