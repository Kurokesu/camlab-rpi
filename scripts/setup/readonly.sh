#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Overlay (read-only) root (Phase 5). Makes the rootfs tolerant of yanked power,
# the field failure mode for a bench tool. Read-only eMMC lower + tmpfs (RAM)
# upper via the overlayroot package, so every write to / lands in RAM and is gone
# on reboot. The box cannot corrupt its own root.
#
# The kiosk still needs to remember its per-sensor mode/fps selection, so a small
# loopback ext4 image on the writable boot partition is mounted at the service
# state dir (/var/lib/camlab), outside the overlay. overlayroot uses recurse=0
# so it never forces that mount read-only.
#
# This script STAGES everything writable but does NOT lock the box down inline.
# It installs and enables a one-shot finaliser (camlab-readonly-firstboot) that
# locks down on the next boot, after first-boot tasks settle. So a normal
# install.sh + one reboot ends up read-only with no extra operator steps.
#
# Stages, each idempotent:
#   packages   overlayroot + initramfs tooling
#   data       loopback image -> /var/lib/camlab (fstab, survives the overlay)
#   overlay    overlayroot.local.conf + auto_initramfs + update-initramfs
#   swap       force zram swap (no swapfile under the overlay)
#   finalise   install the finaliser script + arm the one-shot unit
#
# Safe to re-run. Requires sudo. --revert undoes every stage and unlocks the box
# (the unlock takes effect on the next reboot).
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
# The data is a few hundred bytes of JSON. 32MB is already wildly generous and
# fits the cramped FAT boot partition (which cannot hold sparse files, so the
# image costs its full size on disk).
DATA_SIZE_MB="${CAMLAB_DATA_SIZE_MB:-32}"
OVERLAY_CONF="/etc/overlayroot.local.conf"
# Drop-in that forces the legacy mount API. Without it, systemd-remount-fs fails
# under overlayroot on Trixie (the new kernel mount API cannot reconfigure an
# overlay mount, exit 32), leaving the box in 'degraded' state with FAILED lines
# on the console. See systemd issue #39558.
REMOUNT_DROPIN="/etc/systemd/system.conf.d/overlayfs.conf"
# rpi-swap drop-in. Trixie defaults to a /var/swap file (Mechanism auto picks
# zram+file), which cannot be created under the tmpfs overlay (truncate fails,
# No space left) and loops on restart. Force plain zram: compressed RAM swap, no
# disk writes, no eMMC wear, and it removes any stale swapfile itself.
SWAP_DROPIN="/etc/rpi/swap.conf.d/camlab-readonly.conf"
FINALISE_SCRIPT="/usr/local/sbin/camlab-readonly-finalise"
ONESHOT_UNIT="camlab-readonly-firstboot.service"

# Managed-block markers, same convention as boot.sh, so edits to shared files are
# greppable, idempotent and cleanly removable.
BEGIN="# >>> camlab readonly (do not edit) >>>"
END="# <<< camlab readonly <<<"

REPO_DIR="$(resolve_repo_dir)"

# Stage 1: packages
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

# Stage 2: loopback data partition mounted at /var/lib/camlab.
# Created on the writable boot partition so it survives the read-only overlay.
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
        # FAT cannot store sparse files, so the image costs its full size. Make
        # sure it fits before we start writing, for a clear error if it does not.
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

    # fstab line via managed block. The loop option + nofail keeps a missing image
    # from blocking boot; x-systemd ordering puts the mount before the service.
    block_write /etc/fstab "$BEGIN" "$END" \
        "$DATA_IMG $DATA_MNT ext4 loop,nofail,x-systemd.before=camlab.service 0 2"
    log "ensured fstab mount $DATA_IMG -> $DATA_MNT"

    # Mount now (writable root) and migrate any existing state into the image, so
    # nothing is lost and camlab can read/write straight away.
    mkdir -p "$DATA_MNT"
    if ! mountpoint -q "$DATA_MNT"; then
        # Preserve pre-existing contents across the first mount.
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
    # Marker the one-shot unit keys off (ConditionPathExists) to confirm the
    # writable data mount is actually live before it ever locks the box down.
    touch "$DATA_MNT/.camlab-data"
    chown "$CAMLAB_USER":"$CAMLAB_USER" "$DATA_MNT" 2>/dev/null || true
}

# Stage 3: overlay configuration (staged, not active until the finaliser locks in).
stage_overlay() {
    log "Stage: overlay config"
    if [ "$REVERT" -eq 1 ]; then
        [ -f "$OVERLAY_CONF" ] && { rm -f "$OVERLAY_CONF"; log "removed $OVERLAY_CONF"; }
        [ -f "$REMOUNT_DROPIN" ] && { rm -f "$REMOUNT_DROPIN"; log "removed $REMOUNT_DROPIN"; }
        block_strip "$CONFIG_TXT" "$BEGIN" "$END"
        # Drop the disable token too, so a clean revert leaves cmdline.txt as it was.
        cmdline_remove "$CMDLINE_TXT" "overlayroot=disabled"
        update-initramfs -u >/dev/null 2>&1 || true
        log "overlay config removed (reboot to fully unlock)"
        return
    fi

    # recurse=0 is load-bearing: without it the overlay drives every other mount
    # (including our /var/lib/camlab loop) read-only too.
    atomic_write "$OVERLAY_CONF" 'overlayroot="tmpfs:recurse=0"'$'\n'
    log "wrote $OVERLAY_CONF (tmpfs:recurse=0)"

    # Force the legacy mount API so systemd-remount-fs does not fail under the
    # overlay (Trixie/systemd #39558). Keeps the box out of 'degraded' state and
    # off the FAILED console lines.
    install -d -m 0755 "$(dirname "$REMOUNT_DROPIN")"
    atomic_write "$REMOUNT_DROPIN" \
        '[Manager]'$'\n''DefaultEnvironment="LIBMOUNT_FORCE_MOUNT2=always"'$'\n'
    log "wrote $REMOUNT_DROPIN (legacy mount API for remount-fs)"

    # auto_initramfs=1 makes the firmware load the initramfs that carries the
    # overlay hook. Managed block so it is reversible.
    block_write "$CONFIG_TXT" "$BEGIN" "$END" "auto_initramfs=1"
    log "config.txt: enabled auto_initramfs"

    # Stage the overlay DISABLED. The first post-install boot then comes up
    # writable so first-boot tasks settle; the one-shot finaliser removes this
    # token and reboots to bring the overlay up for real. Without this, the
    # overlay would engage on the very next boot with no writable settle-boot.
    if ! cmdline_has "$CMDLINE_TXT" "overlayroot=disabled"; then
        cmdline_add "$CMDLINE_TXT" "overlayroot=disabled"
        log "cmdline.txt: staged overlay disabled (writable settle-boot)"
    else
        log "cmdline.txt: overlay-disabled token already present"
    fi

    update-initramfs -u >/dev/null
    log "refreshed initramfs"
}

# Stage 4: swap mechanism. Switch rpi-swap to plain zram so it never tries to
# write a swapfile onto the read-only/tmpfs root.
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

# Stage 5: finaliser script + one-shot unit. This is what actually locks the box
# down, on the NEXT boot, after the writable settle-boot. Must be the last stage.
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
    # The finaliser: verify the data mount is healthy, flip overlayroot on by
    # removing any disable token, disable ITSELF first (no reboot loop), reboot.
    cat > "$FINALISE_SCRIPT" <<'FINEOF'
#!/usr/bin/bash
# Installed by scripts/setup/readonly.sh. Locks the root read-only on first boot
# after install, then disables itself and reboots once. Do not edit by hand.
set -euo pipefail
PATH="/usr/sbin:/sbin:/usr/bin:/bin:$PATH"
FW_DIR="/boot/firmware"
CMDLINE="$FW_DIR/cmdline.txt"

logger -t camlab-readonly "finaliser starting"

# Only act during the staged-but-disabled settle-boot. If the disable token is
# gone the overlay is already engaged (or was never staged) - nothing to do.
if ! grep -q 'overlayroot=disabled' "$CMDLINE"; then
    logger -t camlab-readonly "overlay not in disabled-settle state, nothing to do"
    systemctl disable camlab-readonly-firstboot.service >/dev/null 2>&1 || true
    exit 0
fi

# Refuse to lock down unless the persistent data mount is actually writable,
# otherwise the kiosk would lose its state with no way to write it.
if ! mountpoint -q /var/lib/camlab; then
    logger -t camlab-readonly "ABORT: /var/lib/camlab not mounted, leaving box writable"
    exit 1
fi
if ! touch /var/lib/camlab/.write-probe 2>/dev/null; then
    logger -t camlab-readonly "ABORT: /var/lib/camlab not writable, leaving box writable"
    exit 1
fi
rm -f /var/lib/camlab/.write-probe

# Clear any leftover maintenance disable token so the overlay engages next boot.
if grep -q 'overlayroot=disabled' "$CMDLINE"; then
    mount -o remount,rw "$FW_DIR" 2>/dev/null || true
    sed -i 's/ *overlayroot=disabled//g' "$CMDLINE"
    logger -t camlab-readonly "cleared overlayroot=disabled from cmdline"
fi

# Disable self BEFORE rebooting so a wedged overlay can never loop-reboot.
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
