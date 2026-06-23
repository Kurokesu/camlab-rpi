#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# camtestctl - control tool for camtest.service.
# Use for post-install verification, dev iteration, and field support.
#
# Usage:
#   camtestctl start
#   camtestctl stop
#   camtestctl restart
#   camtestctl status
#   camtestctl logs [journalctl-args]     default: last 200 lines
#   camtestctl log-level <level>          trace|debug|info|warn|error|off
#   camtestctl shot [path]                screenshot the live kiosk (needs grim)
#   camtestctl rw                         remount root read-write   (Phase 5)
#   camtestctl ro                         remount root read-only    (Phase 5)
#   camtestctl help
#
# log-level writes a systemd drop-in (Environment=CAMTEST_LOG_LEVEL). The app
# reads this on startup - restart the service to apply.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
CAMTEST_TAG="camtestctl"

# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

SERVICE="camtest.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
LOG_LEVEL_DROPIN="${DROPIN_DIR}/log-level.conf"

cmd_start()   { sudo systemctl start   "$SERVICE"; }
cmd_stop()    { sudo systemctl stop    "$SERVICE"; }
cmd_restart() { sudo systemctl restart "$SERVICE"; }
cmd_status()  { systemctl status "$SERVICE" --no-pager; }

cmd_logs() {
    local args=(-u "$SERVICE" -t camtest --no-pager)
    if [ "$#" -eq 0 ]; then
        args+=(-n 200)
    else
        args+=("$@")
    fi
    journalctl "${args[@]}"
}

cmd_log_level() {
    local level="${1:-}"
    case "$level" in
        trace|debug|info|warn|error|off) ;;
        "") die "log-level requires an argument (trace|debug|info|warn|error|off)" ;;
        *)  die "invalid log level '$level' (trace|debug|info|warn|error|off)" ;;
    esac

    sudo install -d -m 0755 "$DROPIN_DIR"
    sudo tee "$LOG_LEVEL_DROPIN" >/dev/null <<EOF
[Service]
Environment=CAMTEST_LOG_LEVEL=$level
EOF
    sudo systemctl daemon-reload
    log "wrote $LOG_LEVEL_DROPIN (CAMTEST_LOG_LEVEL=$level)"
    log "restart to apply: camtestctl restart"
}

cmd_shot() {
    command -v grim >/dev/null || die "grim not installed (sudo apt install grim)"
    local out="${1:-/tmp/camtest-$(date +%Y%m%d-%H%M%S).png}"
    local sock
    sock="$(ls -1 "/run/user/$CAMTEST_UID"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -n1)"
    [ -n "$sock" ] || die "no wayland socket for uid $CAMTEST_UID (is camtest running?)"
    XDG_RUNTIME_DIR="/run/user/$CAMTEST_UID" WAYLAND_DISPLAY="$(basename "$sock")" grim "$out"
    log "saved $out"
}

cmd_rw() { warn "read-only root not configured yet (Phase 5)."; }
cmd_ro() { warn "read-only root not configured yet (Phase 5)."; }

cmd="${1:-help}"
shift || true

case "$cmd" in
    start)          cmd_start ;;
    stop)           cmd_stop ;;
    restart)        cmd_restart ;;
    status)         cmd_status ;;
    logs)           cmd_logs "$@" ;;
    log-level)      cmd_log_level "$@" ;;
    shot)           cmd_shot "$@" ;;
    rw)             cmd_rw ;;
    ro)             cmd_ro ;;
    -h|--help|help) help_text ;;
    *)              die "unknown command: $cmd (try 'camtestctl help')" ;;
esac
