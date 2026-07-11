#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
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
#   camlabctl net <on|off|status>        toggle networking (off for production,
#                                         on for SSH dev)
#   camlabctl rw                         boot writable next time
#   camlabctl ro                         boot read-only next time
#   camlabctl help
#
# log-level writes a systemd drop-in (Environment=CAMLAB_LOG_LEVEL). The app
# reads this on startup - restart the service to apply.

set -euo pipefail

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# shellcheck disable=SC2034  # log tag read by common.sh
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
    sock=""
    for s in "/run/user/$CAMLAB_UID"/wayland-*; do
        [ -e "$s" ] || continue
        case "$s" in *.lock) continue ;; esac
        sock="$s"
        break
    done
    [ -n "$sock" ] || die "no wayland socket for uid $CAMLAB_UID (is camlab running?)"
    XDG_RUNTIME_DIR="/run/user/$CAMLAB_UID" WAYLAND_DISPLAY="$(basename "$sock")" grim "$out"
    log "saved $out"
}

# Networking toggle: off for production (faster boot), on for SSH dev. Units
# are masked, not just disabled, so a dependency can't pull them back in. The
# *-wait-online units gate only network-online.target (unused by the kiosk)
# and stay masked even after 'net on', so re-enabling never slows boot down.
NET_MANAGERS=(
    NetworkManager.service
    wpa_supplicant.service
    systemd-networkd.service
)
NET_WAIT_UNITS=(
    NetworkManager-wait-online.service
    systemd-networkd-wait-online.service
)

_net_present() { systemctl list-unit-files "$1" >/dev/null 2>&1; }

_overlay_active() { [ "$(findmnt -no FSTYPE / 2>/dev/null)" = "overlay" ]; }

# Under overlayroot, systemctl writes land in the tmpfs upper and vanish on
# reboot. Repeat the change against the lower root so it persists. The verbs
# used here only manage symlinks, so the chroot needs no running systemd.
_persist_net() {
    _overlay_active || return 0
    if ! command -v overlayroot-chroot >/dev/null 2>&1; then
        warn "overlayroot-chroot not found: change is live but will NOT survive a reboot"
        return 0
    fi
    sudo overlayroot-chroot systemctl "$@" >/dev/null 2>&1 || true
}

cmd_net() {
    local action="${1:-status}"
    case "$action" in
        on)
            for u in "${NET_MANAGERS[@]}"; do
                _net_present "$u" || continue
                sudo systemctl unmask "$u" >/dev/null 2>&1 || true
                _persist_net unmask "$u"
                # Unmask only: stock RPi OS ships networkd disabled and NM owns
                # the interfaces. Starting both invites a manager conflict.
                [ "$u" = "systemd-networkd.service" ] && continue
                sudo systemctl enable --now "$u" >/dev/null 2>&1 || true
                _persist_net enable "$u"
            done
            log "networking ON (unmasked + started). For dev/SSH."
            ;;
        off)
            for u in "${NET_MANAGERS[@]}" "${NET_WAIT_UNITS[@]}"; do
                _net_present "$u" || continue
                sudo systemctl disable --now "$u" >/dev/null 2>&1 || true
                sudo systemctl mask "$u" >/dev/null 2>&1 || true
                _persist_net disable "$u"
                _persist_net mask "$u"
            done
            warn "networking OFF (stopped + masked). Reverse with: camlabctl net on"
            warn "Wi-Fi drops now. Ethernet keeps its address until reboot, so an"
            warn "SSH session over Ethernet survives as a grace period."
            ;;
        status)
            local any=0 en act
            for u in "${NET_MANAGERS[@]}" "${NET_WAIT_UNITS[@]}"; do
                _net_present "$u" || continue
                any=1
                # is-enabled/is-active print the state word but exit nonzero for
                # disabled/inactive units. || true keeps set -e out of the way.
                en="$(systemctl is-enabled "$u" 2>/dev/null || true)"; [ -n "$en" ] || en="n/a"
                act="$(systemctl is-active "$u" 2>/dev/null || true)"; [ -n "$act" ] || act="inactive"
                printf "%-42s %s / %s\n" "$u" "$en" "$act"
            done
            [ "$any" -eq 1 ] || log "no known network units present on this box"
            ;;
        *) die "net: expected on|off|status (got '$action')" ;;
    esac
}

# Read-only root toggle. An overlayroot=disabled token in cmdline.txt means
# boot writable, absent means read-only. Flip the token (remounting the boot
# partition rw to do so), takes effect on next reboot.
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
