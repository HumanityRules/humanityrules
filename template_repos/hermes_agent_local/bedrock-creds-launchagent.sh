#!/usr/bin/env bash
# macOS LaunchAgent for refresh-bedrock-creds.sh (every 10 minutes, not at login).
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "LaunchAgent control is macOS-only; run refresh-bedrock-creds.sh from cron on other platforms" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFRESH_SCRIPT="$SCRIPT_DIR/refresh-bedrock-creds.sh"
LABEL="com.coursehero.opsh-bedrock"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/hermes-agent-local"
GUI_DOMAIN="gui/$(id -u)"

usage() {
    cat <<EOF
Usage: $(basename "$0") <command>

  install   Write ~/Library/LaunchAgents/${LABEL}.plist (does not load it)
  start     Install plist if needed, then load the timer
  stop      Unload the timer
EOF
}

launchagent_path() {
    local dir
    dir="$(dirname "$1")"
    if [ "$dir" = "." ]; then
        return
    fi
    printf '%s\n' "$dir"
}

build_launchagent_path() {
    local opsh_bin="$1"
    local aws_bin
    aws_bin="$(command -v aws 2>/dev/null || true)"
    local -a parts=(
        "$(launchagent_path "$opsh_bin")"
        "$(launchagent_path "$aws_bin")"
        /opt/homebrew/bin
        /usr/local/bin
        /usr/bin
        /bin
        /usr/sbin
        /sbin
    )
    local part seen=""
    for part in "${parts[@]}"; do
        [ -n "$part" ] || continue
        case ":$seen:" in
            *":$part:"*) continue ;;
        esac
        seen="${seen:+$seen:}$part"
    done
    printf '%s' "$seen"
}

write_plist() {
    mkdir -p "$LOG_DIR"
    chmod +x "$REFRESH_SCRIPT"

    local opsh_bin="${OPSH_BIN:-$(command -v opsh 2>/dev/null || true)}"
    if [ -z "$opsh_bin" ]; then
        echo "opsh not found on PATH; install CourseHero ops-console or set OPSH_BIN before install/start" >&2
        exit 1
    fi
    local launch_path
    launch_path="$(build_launchagent_path "$opsh_bin")"

    cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${REFRESH_SCRIPT}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OPSH_BIN</key>
    <string>${opsh_bin}</string>
    <key>PATH</key>
    <string>${launch_path}</string>
    <key>AWS_PROFILE</key>
    <string>bedrock_dev</string>
  </dict>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/bedrock-creds.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/bedrock-creds.err</string>
</dict>
</plist>
EOF
}

cmd_install() {
    write_plist
    echo "Installed ${PLIST_PATH} (every 10 minutes; not loaded — run '$(basename "$0") start')"
    echo "Logs: ${LOG_DIR}/bedrock-creds.{log,err}"
}

cmd_start() {
    write_plist
    launchctl bootout "${GUI_DOMAIN}/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "$GUI_DOMAIN" "$PLIST_PATH"
    echo "Started ${LABEL} (every 10 minutes; first run after interval unless you run refresh-bedrock-creds.sh)"
    echo "Logs: ${LOG_DIR}/bedrock-creds.{log,err}"
}

cmd_stop() {
    if launchctl bootout "${GUI_DOMAIN}/${LABEL}" 2>/dev/null; then
        echo "Stopped ${LABEL}"
    else
        echo "${LABEL} was not loaded"
    fi
}

case "${1:-}" in
    install) cmd_install ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    *)
        usage >&2
        exit 1
        ;;
esac
