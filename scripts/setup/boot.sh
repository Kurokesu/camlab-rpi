#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Boot-time tuning: trims power-on to first preview by cutting work the kiosk
# never needs. Disables Bluetooth in config.txt (managed block) and masks unused
# systemd units (network-wait, BT, ModemManager, cloud-init). Silences the
# console for kiosk boot (quiet cmdline, no getty on tty1, no status wall).
# Deliberately left alone: journald/logind/avahi and networking.
# Safe to re-run. Requires sudo. Changes take hold after a reboot.
#
# Usage:
#   sudo scripts/setup/boot.sh            # apply all stages
#   sudo scripts/setup/boot.sh --revert   # undo every stage
#   sudo scripts/setup/boot.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="boot"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

REVERT=0
for arg in "$@"; do
    case "$arg" in
        --revert) REVERT=1 ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
CONFIG_TXT="$FW_DIR/config.txt"
CMDLINE_TXT="$FW_DIR/cmdline.txt"
CONSOLE_DROPIN="/etc/systemd/system.conf.d/camlab-console.conf"

# Cmdline tokens for a quiet kiosk panel. One token at a time so overlayroot
# and tokens owned by other scripts survive. quiet and logo.nologo both
# suppress the kernel fullscreen logo (splash.sh), so they are removed. The
# fullscreen logo keeps the console clean on its own (bench verified).
CMDLINE_ADD=(
    vt.global_cursor_default=0
)
CMDLINE_REMOVE=(
    console=tty1
    quiet
    logo.nologo
    splash
    plymouth.ignore-serial-consoles
)

# Managed block markers, mirroring camlab.config_manager so the edits are
# greppable, idempotent and cleanly removable.
BEGIN="# >>> camlab boot (do not edit) >>>"
END="# <<< camlab boot <<<"

# systemd units the kiosk never uses (only those present are touched). Left out
# on purpose: journald (logs), logind (Cage/PAM session), and avahi/mDNS (LAN
# name resolution, off the critical path). Drop networking with camlabctl net
# off for production instead of masking those.
MASK_UNITS=(
    NetworkManager-wait-online.service
    systemd-networkd-wait-online.service
    bluetooth.service
    hciuart.service
    ModemManager.service
)
# cloud-init ships on stock RPi OS images and is pure overhead on a fixed appliance.
CLOUDINIT_UNITS=(
    cloud-init-local.service
    cloud-init-network.service
    cloud-init-main.service
    cloud-init.service
    cloud-config.service
    cloud-final.service
    cloud-init.target
)

_unit_present() { systemctl list-unit-files "$1" >/dev/null 2>&1; }

# Managed block carrying firmware-stage tweaks, currently just disable-bt.
stage_config() {
    log "Stage: config.txt (firmware tweaks)"
    [ -f "$CONFIG_TXT" ] || { warn "$CONFIG_TXT missing, skipping"; return; }
    if [ "$REVERT" -eq 1 ]; then
        block_strip "$CONFIG_TXT" "$BEGIN" "$END"
        log "config.txt: removed camlab boot block"
        return
    fi
    block_write "$CONFIG_TXT" "$BEGIN" "$END" $'dtoverlay=disable-bt\ndisable_splash=1'
    log "config.txt: wrote managed block (disable-bt, disable_splash)"
}

stage_systemd() {
    log "Stage: systemd units"
    local u changed=0
    if [ "$REVERT" -eq 1 ]; then
        for u in "${MASK_UNITS[@]}"; do
            _unit_present "$u" || continue
            systemctl unmask "$u" >/dev/null 2>&1 || true
            # Re-enable too. We used disable on apply, so unmask alone would
            # leave the unit installed but disabled. enable is a harmless no-op
            # for static units that have no [Install] section.
            systemctl enable "$u" >/dev/null 2>&1 || true
            changed=1
        done
        for u in "${CLOUDINIT_UNITS[@]}"; do
            _unit_present "$u" || continue
            systemctl enable "$u" >/dev/null 2>&1 || true
            changed=1
        done
        # Lift the cloud-init kill switch we dropped on apply.
        if [ -f /etc/cloud/cloud-init.disabled ]; then
            rm -f /etc/cloud/cloud-init.disabled
            log "removed /etc/cloud/cloud-init.disabled"
            changed=1
        fi
        if [ "$changed" -eq 1 ]; then
            systemctl daemon-reload || true
        fi
        log "systemd: unmasked/re-enabled units (reboot to take effect)"
        return
    fi
    for u in "${MASK_UNITS[@]}"; do
        if _unit_present "$u"; then
            systemctl disable --now "$u" >/dev/null 2>&1 || true
            systemctl mask "$u" >/dev/null 2>&1 || true
            log "masked $u"
            changed=1
        fi
    done
    for u in "${CLOUDINIT_UNITS[@]}"; do
        if _unit_present "$u"; then
            systemctl disable "$u" >/dev/null 2>&1 || true
            log "disabled $u"
            changed=1
        fi
    done
    # cloud-init also honours a disable flag, which stops it even when a unit is
    # static or gets re-enabled by a package upgrade.
    if [ -d /etc/cloud ]; then
        touch /etc/cloud/cloud-init.disabled
        log "touched /etc/cloud/cloud-init.disabled"
        changed=1
    fi
    if [ "$changed" -eq 1 ]; then
        systemctl daemon-reload || true
    fi
}

stage_console() {
    log "Stage: console quiet"
    if [ "$REVERT" -eq 1 ]; then
        if [ -f "$CMDLINE_TXT" ]; then
            local t
            for t in "${CMDLINE_ADD[@]}"; do cmdline_remove "$CMDLINE_TXT" "$t"; done
            cmdline_add "$CMDLINE_TXT" "console=tty1"
            cmdline_add "$CMDLINE_TXT" "quiet"
        fi
        rm -f "$CONSOLE_DROPIN"
        systemctl unmask getty@tty1.service >/dev/null 2>&1 || true
        log "console output restored"
        return
    fi
    if [ -f "$CMDLINE_TXT" ]; then
        local t
        for t in "${CMDLINE_REMOVE[@]}"; do cmdline_remove "$CMDLINE_TXT" "$t"; done
        for t in "${CMDLINE_ADD[@]}"; do cmdline_add "$CMDLINE_TXT" "$t"; done
        log "cmdline.txt: quiet kiosk tokens applied"
    else
        warn "$CMDLINE_TXT missing, skipping cmdline quiet"
    fi
    install -d -m 0755 "$(dirname "$CONSOLE_DROPIN")"
    atomic_write "$CONSOLE_DROPIN" '[Manager]'$'\n''ShowStatus=no'$'\n'
    chmod 0644 "$CONSOLE_DROPIN"
    systemctl mask getty@tty1.service >/dev/null 2>&1 || true
}

if [ "$REVERT" -eq 1 ]; then
    header "Boot tuning - reverting all stages"
else
    header "Boot tuning - applying (headless kiosk)"
fi

stage_config
stage_systemd
stage_console

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to restore stock boot behaviour."
else
    log "Done."
fi
