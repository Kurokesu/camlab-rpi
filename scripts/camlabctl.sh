#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# camlabctl - control tool for camlab.service.
# Use for post-install verification, dev iteration, and field support.
#
# Usage:
#   camlabctl start
#   camlabctl stop
#   camlabctl restart
#   camlabctl status
#   camlabctl logs [journalctl-args]     default: last 200 lines
#   camlabctl log-level <level>          trace|debug|info|warn|error|off
#   camlabctl shot [path]                screenshot the live kiosk (needs grim)
#   camlabctl net <on|off|status>        toggle networking (reversible, off for
#                                         production, on for SSH dev)
#   camlabctl rw                         remount root read-write   (Phase 5)
#   camlabctl ro                         remount root read-only    (Phase 5)
#   camlabctl help
#
# log-level writes a systemd drop-in (Environment=CAMLAB_LOG_LEVEL). The app
# reads this on startup - restart the service to apply.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
CAMLAB_TAG="camlabctl"

# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

SERVICE="camlab.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
LOG_LEVEL_DROPIN="${DROPIN_DIR}/log-level.conf"

cmd_start()   { sudo systemctl start   "$SERVICE"; }
cmd_stop()    { sudo systemctl stop    "$SERVICE"; }
cmd_restart() { sudo systemctl restart "$SERVICE"; }
cmd_status()  { systemctl status "$SERVICE" --no-pager; }

cmd_logs() {
    local args=(-u "$SERVICE" -t camlab --no-pager)
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
Environment=CAMLAB_LOG_LEVEL=$level
EOF
    sudo systemctl daemon-reload
    log "wrote $LOG_LEVEL_DROPIN (CAMLAB_LOG_LEVEL=$level)"
    log "restart to apply: camlabctl restart"
}

cmd_shot() {
    command -v grim >/dev/null || die "grim not installed (sudo apt install grim)"
    local out="${1:-/tmp/camlab-$(date +%Y%m%d-%H%M%S).png}"
    local sock
    sock="$(ls -1 "/run/user/$CAMLAB_UID"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -n1)"
    [ -n "$sock" ] || die "no wayland socket for uid $CAMLAB_UID (is camlab running?)"
    XDG_RUNTIME_DIR="/run/user/$CAMLAB_UID" WAYLAND_DISPLAY="$(basename "$sock")" grim "$out"
    log "saved $out"
}

# Networking toggle. Production runs headless with no network (faster boot, no
# attack surface), flipped back on for remote SSH dev. We mask/unmask rather
# than just disable, so a masked unit can't be pulled in by a dependency
# either. Only acts on units that exist on this box.
NET_UNITS=(
    NetworkManager.service
    NetworkManager-wait-online.service
    wpa_supplicant.service
    systemd-networkd.service
    systemd-networkd-wait-online.service
)

_net_present() { systemctl list-unit-files "$1" >/dev/null 2>&1; }

cmd_net() {
    local action="${1:-status}"
    case "$action" in
        on)
            for u in "${NET_UNITS[@]}"; do
                _net_present "$u" || continue
                sudo systemctl unmask "$u" >/dev/null 2>&1 || true
            done
            # Bring the primary manager up now so SSH survives without a reboot.
            for u in NetworkManager.service systemd-networkd.service wpa_supplicant.service; do
                _net_present "$u" && sudo systemctl enable --now "$u" >/dev/null 2>&1 || true
            done
            log "networking ON (unmasked + started). For dev/SSH."
            ;;
        off)
            for u in "${NET_UNITS[@]}"; do
                _net_present "$u" || continue
                sudo systemctl disable --now "$u" >/dev/null 2>&1 || true
                sudo systemctl mask "$u" >/dev/null 2>&1 || true
            done
            warn "networking OFF (stopped + masked). Reverse with: camlabctl net on"
            warn "if run over SSH, this connection is about to drop."
            ;;
        status)
            local any=0 en act
            for u in "${NET_UNITS[@]}"; do
                _net_present "$u" || continue
                any=1
                # is-enabled/is-active print a word to stdout but exit nonzero
                # for disabled/inactive units. Keep the word, drop the exit code
                # (the trailing || true stops set -e aborting on that exit).
                en="$(systemctl is-enabled "$u" 2>/dev/null || true)"; [ -n "$en" ] || en="n/a"
                act="$(systemctl is-active "$u" 2>/dev/null || true)"; [ -n "$act" ] || act="inactive"
                printf "%-42s %s / %s\n" "$u" "$en" "$act"
            done
            [ "$any" -eq 1 ] || log "no known network units present on this box"
            ;;
        *) die "net: expected on|off|status (got '$action')" ;;
    esac
}

# Read-only root toggle. The overlay is driven by an overlayroot=disabled token on
# the kernel command line: present = writable, absent = read-only. We flip the
# token in cmdline.txt (remounting the boot partition writable to do so) and the
# change takes effect on the next reboot. A no-op if overlayroot was never set up.
FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
CMDLINE="$FW_DIR/cmdline.txt"

_overlay_present() { [ -f /etc/overlayroot.local.conf ]; }

cmd_rw() {
    _overlay_present || { warn "read-only root not set up (run scripts/setup/readonly.sh)"; return; }
    if grep -q 'overlayroot=disabled' "$CMDLINE"; then
        log "already set to boot writable (overlayroot=disabled). Reboot if not already."
        return
    fi
    sudo mount -o remount,rw "$FW_DIR" 2>/dev/null || true
    # Append the token to the single cmdline line (space-separated, no newline).
    sudo sed -i 's/[[:space:]]*$/ overlayroot=disabled/' "$CMDLINE"
    log "writable on next boot. Apply: sudo reboot   (then camlabctl ro to re-lock)"
}

cmd_ro() {
    _overlay_present || { warn "read-only root not set up (run scripts/setup/readonly.sh)"; return; }
    sudo mount -o remount,rw "$FW_DIR" 2>/dev/null || true
    sudo sed -i 's/ *overlayroot=disabled//g' "$CMDLINE"
    log "read-only on next boot. Apply: sudo reboot"
}

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
    net)            cmd_net "$@" ;;
    rw)             cmd_rw ;;
    ro)             cmd_ro ;;
    -h|--help|help) help_text ;;
    *)              die "unknown command: $cmd (try 'camlabctl help')" ;;
esac
