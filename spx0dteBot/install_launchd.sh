#!/bin/bash
#
# install_launchd.sh — Install both launchd jobs for the SPX 0DTE trader.
#
# Usage:
#   ./install_launchd.sh install     # load both jobs
#   ./install_launchd.sh uninstall   # unload both jobs
#   ./install_launchd.sh status      # show current status
#   ./install_launchd.sh logs        # tail all logs
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_DIR="$PROJECT_ROOT/spx0dteBot"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

ENTRY_LABEL="com.spxtrader.entry"
MONITOR_LABEL="com.spxtrader.monitor"

ENTRY_PLIST_SRC="$PLIST_DIR/$ENTRY_LABEL.plist"
MONITOR_PLIST_SRC="$PLIST_DIR/$MONITOR_LABEL.plist"

ENTRY_PLIST_DST="$LAUNCH_AGENTS_DIR/$ENTRY_LABEL.plist"
MONITOR_PLIST_DST="$LAUNCH_AGENTS_DIR/$MONITOR_LABEL.plist"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$PROJECT_ROOT/logs"

usage() {
    echo "Usage: $0 {install|uninstall|status|logs}"
    exit 1
}

cmd_install() {
    echo "Checking plist paths reference real files before installing…"

    PY_PATH=$(python3 -c "import plistlib,sys; d=plistlib.load(open('$ENTRY_PLIST_SRC','rb')); print(d['ProgramArguments'][1])")
    if [[ ! -f "$PY_PATH" ]]; then
        echo "WARNING: entry_runner.py path in plist does not exist: $PY_PATH"
        echo "Edit $ENTRY_PLIST_SRC and fix ProgramArguments / WorkingDirectory before installing."
        exit 1
    fi

    PY_PATH2=$(python3 -c "import plistlib,sys; d=plistlib.load(open('$MONITOR_PLIST_SRC','rb')); print(d['ProgramArguments'][1])")
    if [[ ! -f "$PY_PATH2" ]]; then
        echo "WARNING: monitor_daemon.py path in plist does not exist: $PY_PATH2"
        echo "Edit $MONITOR_PLIST_SRC and fix ProgramArguments / WorkingDirectory before installing."
        exit 1
    fi

    echo "Copying plists to $LAUNCH_AGENTS_DIR …"
    cp "$ENTRY_PLIST_SRC"   "$ENTRY_PLIST_DST"
    cp "$MONITOR_PLIST_SRC" "$MONITOR_PLIST_DST"

    echo "Loading $ENTRY_LABEL …"
    launchctl unload "$ENTRY_PLIST_DST" 2>/dev/null || true
    launchctl load "$ENTRY_PLIST_DST"

    echo "Loading $MONITOR_LABEL …"
    launchctl unload "$MONITOR_PLIST_DST" 2>/dev/null || true
    launchctl load "$MONITOR_PLIST_DST"

    echo ""
    echo "Installed. Verify with: $0 status"
}

cmd_uninstall() {
    echo "Unloading jobs…"
    launchctl unload "$ENTRY_PLIST_DST"   2>/dev/null || true
    launchctl unload "$MONITOR_PLIST_DST" 2>/dev/null || true

    echo "Removing plists from $LAUNCH_AGENTS_DIR …"
    rm -f "$ENTRY_PLIST_DST" "$MONITOR_PLIST_DST"

    echo "Uninstalled."
}

cmd_status() {
    echo "── $ENTRY_LABEL ──"
    launchctl list | grep "$ENTRY_LABEL" || echo "  not loaded"
    echo ""
    echo "── $MONITOR_LABEL ──"
    launchctl list | grep "$MONITOR_LABEL" || echo "  not loaded"
    echo ""
    echo "Full detail:"
    echo "  launchctl print gui/$(id -u)/$ENTRY_LABEL"
    echo "  launchctl print gui/$(id -u)/$MONITOR_LABEL"
}

cmd_logs() {
    echo "Tailing all logs (Ctrl-C to stop)…"
    tail -f \
        "$PROJECT_ROOT/logs/entry_runner.stdout.log" \
        "$PROJECT_ROOT/logs/entry_runner.stderr.log" \
        "$PROJECT_ROOT/logs/monitor_daemon.stdout.log" \
        "$PROJECT_ROOT/logs/monitor_daemon.stderr.log" \
        2>/dev/null
}

case "${1:-}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    status)    cmd_status ;;
    logs)      cmd_logs ;;
    *)         usage ;;
esac
