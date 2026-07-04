#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Boot-time tuning for the kiosk. Trims power-on to first preview on a CM5 by
# cutting work that a single-purpose headless bench tool never needs. Two
# stages, each idempotent and reversible:
#   A. config.txt disable Bluetooth (managed block, the bench never uses BT)
#   B. systemd    mask network-wait + BT + ModemManager, neutralise cloud-init
#                 (only units present, never journald/logind/avahi-mDNS)
# We deliberately leave console/bootloader logging on (BOOT_UART, no quiet): for
# an internal bench tool the boot log is useful operator feedback while waiting,
# and disabling it bought no measurable time.
# Networking stays under operator control via camlabctl net on|off, not here,
# so dev keeps SSH. Re-running is a no-op. --revert undoes every stage.
# Safe to re-run. Requires sudo. A reboot is needed for the changes to take hold.
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

# Atomic write preserving mode/owner of an existing file.
_atomic_write() {
    local path="$1" content="$2" tmp
    tmp="$(mktemp "${path}.camlab-XXXXXX")"
    printf '%s' "$content" > "$tmp"
    if [ -f "$path" ]; then chmod --reference="$path" "$tmp" 2>/dev/null || true; fi
    mv -f "$tmp" "$path"
}

# Stage A: config.txt
# Managed block carrying firmware-stage tweaks, currently just disable-bt.
stage_config() {
    [ -f "$CONFIG_TXT" ] || { warn "A) $CONFIG_TXT missing, skipping"; return; }
    local text kept
    text="$(cat "$CONFIG_TXT")"
    kept="$(printf '%s\n' "$text" | sed "/^${BEGIN}$/,/^${END}$/d")"
    kept="${kept%$'\n'}"  # trim one trailing newline before we re-add spacing
    if [ "$REVERT" -eq 1 ]; then
        _atomic_write "$CONFIG_TXT" "${kept}"$'\n'
        log "A) config.txt: removed camlab boot block"
        return
    fi
    local block
    block="$(printf '%s\n%s\n%s' "$BEGIN" "dtoverlay=disable-bt" "$END")"
    _atomic_write "$CONFIG_TXT" "${kept}"$'\n\n'"${block}"$'\n'
    log "A) config.txt: wrote managed block (dtoverlay=disable-bt)"
}

# Stage B: systemd
stage_systemd() {
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
            log "B) removed /etc/cloud/cloud-init.disabled"
            changed=1
        fi
        if [ "$changed" -eq 1 ]; then
            systemctl daemon-reload || true
        fi
        log "B) systemd: unmasked/re-enabled units (reboot to take effect)"
        return
    fi
    for u in "${MASK_UNITS[@]}"; do
        if _unit_present "$u"; then
            systemctl disable --now "$u" >/dev/null 2>&1 || true
            systemctl mask "$u" >/dev/null 2>&1 || true
            log "B) masked $u"
            changed=1
        fi
    done
    for u in "${CLOUDINIT_UNITS[@]}"; do
        if _unit_present "$u"; then
            systemctl disable "$u" >/dev/null 2>&1 || true
            log "B) disabled $u"
            changed=1
        fi
    done
    # cloud-init also honours a disable flag, which stops it even when a unit is
    # static or gets re-enabled by a package upgrade.
    if [ -d /etc/cloud ]; then
        touch /etc/cloud/cloud-init.disabled
        log "B) touched /etc/cloud/cloud-init.disabled"
        changed=1
    fi
    if [ "$changed" -eq 1 ]; then
        systemctl daemon-reload || true
    fi
}

if [ "$REVERT" -eq 1 ]; then
    header "Boot tuning - reverting all stages"
else
    header "Boot tuning - applying (CM5 headless kiosk)"
fi

stage_config
stage_systemd

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to restore stock boot behaviour."
else
    log "Done. Reboot to measure: sudo reboot"
    log "Networking is unchanged here. Toggle it with: camlabctl net off|on"
fi
