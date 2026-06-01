#!/usr/bin/env bash
# macOS LaunchAgent for refresh-bedrock-creds.sh (every 10 minutes, not at login).
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "LaunchAgent control is macOS-only; run refresh-bedrock-creds.sh from cron on other platforms" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFRESH_SCRIPT="$SCRIPT_DIR/refresh-bedrock-creds.sh"
LABEL="com.humanityrules.opsh-bedrock"
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

write_plist() {
    mkdir -p "$LOG_DIR"
    chmod +x "$REFRESH_SCRIPT"

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
