#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Read-only root via overlayroot (tmpfs upper, eMMC lower). Survives power loss.
# Persistent state on loopback ext4 at /var/lib/camlab (writable boot partition).
# Stages config only. One-shot finaliser locks down on next boot after settle.
#
# Stages: packages, data, overlay, swap, finalise
#
# Safe to re-run. Requires sudo. --revert unlocks on next reboot.
#
# Usage:
#   sudo scripts/setup/readonly.sh           # stage read-only, arm the finaliser
#   sudo scripts/setup/readonly.sh --revert  # undo + unlock (reboot to apply)
#   sudo scripts/setup/readonly.sh --help

set -euo pipefail

# mkfs/losetup/blkid live in sbin, off the non-login PATH.
PATH="/usr/sbin:/sbin:/usr/bin:/bin:$PATH"

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="readonly"

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
DATA_IMG="$FW_DIR/camlab-data.img"
DATA_MNT="/var/lib/camlab"
# State is a few hundred bytes of JSON. 32MB fits cramped FAT boot partition.
DATA_SIZE_MB="${CAMLAB_DATA_SIZE_MB:-32}"
OVERLAY_CONF="/etc/overlayroot.local.conf"
# Legacy mount API drop-in. systemd-remount-fs fails under overlayroot on Trixie without it.
REMOUNT_DROPIN="/etc/systemd/system.conf.d/overlayfs.conf"
# Force zram swap. /var/swap file cannot live under tmpfs overlay.
SWAP_DROPIN="/etc/rpi/swap.conf.d/camlab-readonly.conf"
FINALISE_SCRIPT="/usr/local/sbin/camlab-readonly-finalise"
ONESHOT_UNIT="camlab-readonly-firstboot.service"

# Managed-block markers, same convention as boot.sh.
BEGIN="# >>> camlab readonly (do not edit) >>>"
END="# <<< camlab readonly <<<"

REPO_DIR="$(resolve_repo_dir)"

stage_packages() {
    log "Stage: packages"
    if [ "$REVERT" -eq 1 ]; then
        log "leaving overlayroot package installed (harmless, removal is manual)"
        return
    fi
    if dpkg -s overlayroot >/dev/null 2>&1; then
        log "overlayroot already installed"
        return
    fi
    log "installing overlayroot + initramfs tooling"
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        overlayroot initramfs-tools busybox-static >/dev/null
}

# Loopback data image on writable boot partition. Survives read-only overlay.
stage_data() {
    log "Stage: data partition"
    if [ "$REVERT" -eq 1 ]; then
        if mountpoint -q "$DATA_MNT"; then
            umount "$DATA_MNT" 2>/dev/null || true
        fi
        block_strip /etc/fstab "$BEGIN" "$END"
        if [ -f "$DATA_IMG" ]; then
            rm -f "$DATA_IMG"
            log "removed $DATA_IMG and its fstab line"
        else
            log "no data image to remove"
        fi
        return
    fi

    if [ ! -f "$DATA_IMG" ]; then
        # FAT has no sparse files. Check room up front.
        local free_mb
        free_mb="$(df -m --output=avail "$FW_DIR" | tail -1 | tr -d ' ')"
        if [ "$free_mb" -lt "$((DATA_SIZE_MB + 16))" ]; then
            die "not enough room on $FW_DIR (${free_mb}MB free, need ~$((DATA_SIZE_MB + 16))MB). Lower CAMLAB_DATA_SIZE_MB."
        fi
        log "creating ${DATA_SIZE_MB}MB data image at $DATA_IMG"
        truncate -s "${DATA_SIZE_MB}M" "$DATA_IMG"
        mkfs.ext4 -q -L camlab-data "$DATA_IMG"
    else
        log "data image already present, keeping it"
    fi

    # nofail: missing image must not block boot. x-systemd.before orders before service.
    block_write /etc/fstab "$BEGIN" "$END" \
        "$DATA_IMG $DATA_MNT ext4 loop,nofail,x-systemd.before=camlab.service 0 2"
    log "ensured fstab mount $DATA_IMG -> $DATA_MNT"

    # Mount now, migrate existing state into image.
    mkdir -p "$DATA_MNT"
    if ! mountpoint -q "$DATA_MNT"; then
        # Mount hides existing dir. Copy back after mount.
        local staged=""
        if [ -n "$(ls -A "$DATA_MNT" 2>/dev/null)" ]; then
            staged="$(mktemp -d)"
            cp -a "$DATA_MNT/." "$staged/"
        fi
        mount "$DATA_MNT"
        if [ -n "$staged" ]; then
            cp -a "$staged/." "$DATA_MNT/" 2>/dev/null || true
            rm -rf "$staged"
        fi
        log "mounted $DATA_MNT (migrated existing state if any)"
    fi
    # Marker for one-shot ConditionPathExists (data mount live before lockdown).
    touch "$DATA_MNT/.camlab-data"
    chown "$CAMLAB_USER":"$CAMLAB_USER" "$DATA_MNT" 2>/dev/null || true
}

# Overlay config. Inert until finaliser locks in.
stage_overlay() {
    log "Stage: overlay config"
    if [ "$REVERT" -eq 1 ]; then
        [ -f "$OVERLAY_CONF" ] && { rm -f "$OVERLAY_CONF"; log "removed $OVERLAY_CONF"; }
        [ -f "$REMOUNT_DROPIN" ] && { rm -f "$REMOUNT_DROPIN"; log "removed $REMOUNT_DROPIN"; }
        block_strip "$CONFIG_TXT" "$BEGIN" "$END"
        # Drop disable token so revert leaves cmdline as-is.
        cmdline_remove "$CMDLINE_TXT" "overlayroot=disabled"
        update-initramfs -u >/dev/null 2>&1 || true
        log "overlay config removed (reboot to fully unlock)"
        return
    fi

    # recurse=0: do not force /var/lib/camlab loop mount read-only.
    atomic_write "$OVERLAY_CONF" 'overlayroot="tmpfs:recurse=0"'$'\n'
    log "wrote $OVERLAY_CONF (tmpfs:recurse=0)"

    # Legacy mount API or systemd-remount-fs fails under overlay.
    install -d -m 0755 "$(dirname "$REMOUNT_DROPIN")"
    atomic_write "$REMOUNT_DROPIN" \
        '[Manager]'$'\n''DefaultEnvironment="LIBMOUNT_FORCE_MOUNT2=always"'$'\n'
    log "wrote $REMOUNT_DROPIN (legacy mount API for remount-fs)"

    # auto_initramfs=1: firmware loads initramfs with overlay hook.
    block_write "$CONFIG_TXT" "$BEGIN" "$END" "auto_initramfs=1"
    log "config.txt: enabled auto_initramfs"

    # Stage overlay disabled for writable settle-boot. Finaliser clears token and reboots.
    if ! cmdline_has "$CMDLINE_TXT" "overlayroot=disabled"; then
        cmdline_add "$CMDLINE_TXT" "overlayroot=disabled"
        log "cmdline.txt: staged overlay disabled (writable settle-boot)"
    else
        log "cmdline.txt: overlay-disabled token already present"
    fi

    update-initramfs -u >/dev/null
    log "refreshed initramfs"
}

# zram swap only. No swapfile on tmpfs root.
stage_swap() {
    log "Stage: swap"
    if [ "$REVERT" -eq 1 ]; then
        [ -f "$SWAP_DROPIN" ] && { rm -f "$SWAP_DROPIN"; log "removed $SWAP_DROPIN"; }
        return
    fi
    install -d -m 0755 "$(dirname "$SWAP_DROPIN")"
    atomic_write "$SWAP_DROPIN" '[Main]'$'\n''Mechanism=zram'$'\n'
    log "wrote $SWAP_DROPIN (zram swap, no swapfile under overlay)"
}

# Finaliser + one-shot unit. Locks down on next boot after settle. Last stage.
stage_finalise() {
    log "Stage: finaliser"
    if [ "$REVERT" -eq 1 ]; then
        systemctl disable "$ONESHOT_UNIT" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$ONESHOT_UNIT" "$FINALISE_SCRIPT"
        systemctl daemon-reload 2>/dev/null || true
        log "removed finaliser + one-shot unit"
        return
    fi

    install -d -m 0755 /usr/local/sbin
    cat > "$FINALISE_SCRIPT" <<'FINEOF'
#!/usr/bin/bash
# Installed by readonly.sh. Locks root read-only on first post-install boot.
set -euo pipefail
PATH="/usr/sbin:/sbin:/usr/bin:/bin:$PATH"
FW_DIR="/boot/firmware"
CMDLINE="$FW_DIR/cmdline.txt"

logger -t camlab-readonly "finaliser starting"

# Only act during disabled-settle boot. No token means overlay already engaged.
if ! grep -q 'overlayroot=disabled' "$CMDLINE"; then
    logger -t camlab-readonly "overlay not in disabled-settle state, nothing to do"
    systemctl disable camlab-readonly-firstboot.service >/dev/null 2>&1 || true
    exit 0
fi

# Refuse lockdown unless /var/lib/camlab is writable.
if ! mountpoint -q /var/lib/camlab; then
    logger -t camlab-readonly "ABORT: /var/lib/camlab not mounted, leaving box writable"
    exit 1
fi
if ! touch /var/lib/camlab/.write-probe 2>/dev/null; then
    logger -t camlab-readonly "ABORT: /var/lib/camlab not writable, leaving box writable"
    exit 1
fi
rm -f /var/lib/camlab/.write-probe

# Clear token so overlay engages next boot.
if grep -q 'overlayroot=disabled' "$CMDLINE"; then
    mount -o remount,rw "$FW_DIR" 2>/dev/null || true
    sed -i 's/ *overlayroot=disabled//g' "$CMDLINE"
    logger -t camlab-readonly "cleared overlayroot=disabled from cmdline"
fi

# Disable self before reboot (avoid loop on wedged overlay).
systemctl disable camlab-readonly-firstboot.service >/dev/null 2>&1 || true
logger -t camlab-readonly "locked in, rebooting into read-only root"
sync
systemctl reboot
FINEOF
    chmod 0755 "$FINALISE_SCRIPT"
    log "installed finaliser $FINALISE_SCRIPT"

    cp "$REPO_DIR/deploy/$ONESHOT_UNIT" "/etc/systemd/system/$ONESHOT_UNIT"
    systemctl daemon-reload
    systemctl enable "$ONESHOT_UNIT" >/dev/null 2>&1 || true
    log "armed $ONESHOT_UNIT (locks down on next boot)"
}

if [ "$REVERT" -eq 1 ]; then
    header "Read-only root - reverting all stages"
else
    header "Read-only root - staging (locks in on next boot)"
fi

stage_packages
stage_data
stage_overlay
stage_swap
stage_finalise

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to come up writable: sudo reboot"
else
    log "Staged. The next reboot settles first-boot tasks, then the finaliser"
    log "locks the root read-only and reboots once more, automatically."
    log "Dev toggle afterwards: camlabctl rw  /  camlabctl ro"
fi
