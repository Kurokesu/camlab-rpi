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
# state dir (/var/lib/camtest), outside the overlay. overlayroot uses recurse=0
# so it never forces that mount read-only.
#
# This script STAGES everything writable but does NOT lock the box down inline.
# It installs and enables a one-shot finaliser (camtest-readonly-firstboot) that
# locks down on the next boot, after first-boot tasks settle. So a normal
# install.sh + one reboot ends up read-only with no extra operator steps.
#
# Stages, each idempotent:
#   1. packages   overlayroot + initramfs tooling
#   2. data       loopback image -> /var/lib/camtest (fstab, survives the overlay)
#   3. overlay    overlayroot.local.conf + auto_initramfs + update-initramfs
#   4. finalise   install the finaliser script + arm the one-shot unit
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

CAMTEST_TAG="readonly"

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

FW_DIR="${CAMTEST_FW_DIR:-/boot/firmware}"
CONFIG_TXT="$FW_DIR/config.txt"
CMDLINE_TXT="$FW_DIR/cmdline.txt"
DATA_IMG="$FW_DIR/camtest-data.img"
DATA_MNT="/var/lib/camtest"
# The data is a few hundred bytes of JSON. 32MB is already wildly generous and
# fits the cramped FAT boot partition (which cannot hold sparse files, so the
# image costs its full size on disk).
DATA_SIZE_MB="${CAMTEST_DATA_SIZE_MB:-32}"
OVERLAY_CONF="/etc/overlayroot.local.conf"
# Drop-in that forces the legacy mount API. Without it, systemd-remount-fs fails
# under overlayroot on Trixie (the new kernel mount API cannot reconfigure an
# overlay mount, exit 32), leaving the box in 'degraded' state with FAILED lines
# on the console. See systemd issue #39558.
REMOUNT_DROPIN="/etc/systemd/system.conf.d/overlayfs.conf"
FINALISE_SCRIPT="/usr/local/sbin/camtest-readonly-finalise"
ONESHOT_UNIT="camtest-readonly-firstboot.service"

# Managed-block markers, same convention as boot.sh, so edits to shared files are
# greppable, idempotent and cleanly removable.
BEGIN="# >>> camtest readonly (do not edit) >>>"
END="# <<< camtest readonly <<<"

REPO_DIR="$(resolve_repo_dir)"

# Atomic write preserving mode of an existing file.
_atomic_write() {
    local path="$1" content="$2" tmp
    tmp="$(mktemp "${path}.camtest-XXXXXX")"
    printf '%s' "$content" > "$tmp"
    if [ -f "$path" ]; then chmod --reference="$path" "$tmp" 2>/dev/null || true; fi
    mv -f "$tmp" "$path"
}

# Drop our managed block from a file (no-op if absent).
_strip_block() {
    local path="$1" kept
    [ -f "$path" ] || return 0
    kept="$(sed "/^${BEGIN}$/,/^${END}$/d" "$path")"
    kept="${kept%$'\n'}"
    _atomic_write "$path" "${kept}"$'\n'
}

# Stage 1: packages
stage_packages() {
    if [ "$REVERT" -eq 1 ]; then
        log "1) leaving overlayroot package installed (harmless; removal is manual)"
        return
    fi
    if dpkg -s overlayroot >/dev/null 2>&1; then
        log "1) overlayroot already installed"
        return
    fi
    log "1) installing overlayroot + initramfs tooling"
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        overlayroot initramfs-tools busybox-static >/dev/null
}

# Stage 2: loopback data partition mounted at /var/lib/camtest.
# Created on the writable boot partition so it survives the read-only overlay.
stage_data() {
    if [ "$REVERT" -eq 1 ]; then
        if mountpoint -q "$DATA_MNT"; then
            umount "$DATA_MNT" 2>/dev/null || true
        fi
        _strip_block /etc/fstab
        if [ -f "$DATA_IMG" ]; then
            rm -f "$DATA_IMG"
            log "2) removed $DATA_IMG and its fstab line"
        else
            log "2) no data image to remove"
        fi
        return
    fi

    if [ ! -f "$DATA_IMG" ]; then
        # FAT cannot store sparse files, so the image costs its full size. Make
        # sure it fits before we start writing, for a clear error if it does not.
        local free_mb
        free_mb="$(df -m --output=avail "$FW_DIR" | tail -1 | tr -d ' ')"
        if [ "$free_mb" -lt "$((DATA_SIZE_MB + 16))" ]; then
            die "2) not enough room on $FW_DIR (${free_mb}MB free, need ~$((DATA_SIZE_MB + 16))MB). Lower CAMTEST_DATA_SIZE_MB."
        fi
        log "2) creating ${DATA_SIZE_MB}MB data image at $DATA_IMG"
        truncate -s "${DATA_SIZE_MB}M" "$DATA_IMG"
        mkfs.ext4 -q -L camtest-data "$DATA_IMG"
    else
        log "2) data image already present, keeping it"
    fi

    # fstab line via managed block. The loop option + nofail keeps a missing image
    # from blocking boot; x-systemd ordering puts the mount before the service.
    if ! grep -qF "$BEGIN" /etc/fstab; then
        local block
        block="$(printf '%s\n%s %s ext4 loop,nofail,x-systemd.before=camtest.service 0 2\n%s' \
                 "$BEGIN" "$DATA_IMG" "$DATA_MNT" "$END")"
        printf '\n%s\n' "$block" >> /etc/fstab
        log "2) added fstab mount $DATA_IMG -> $DATA_MNT"
    else
        log "2) fstab mount already present"
    fi

    # Mount now (writable root) and migrate any existing state into the image, so
    # nothing is lost and camtest can read/write straight away.
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
        log "2) mounted $DATA_MNT (migrated existing state if any)"
    fi
    # Marker the one-shot unit keys off (ConditionPathExists) to confirm the
    # writable data mount is actually live before it ever locks the box down.
    touch "$DATA_MNT/.camtest-data"
    chown "$CAMTEST_USER":"$CAMTEST_USER" "$DATA_MNT" 2>/dev/null || true
}

# Stage 3: overlay configuration (staged, not active until the finaliser locks in).
stage_overlay() {
    if [ "$REVERT" -eq 1 ]; then
        [ -f "$OVERLAY_CONF" ] && { rm -f "$OVERLAY_CONF"; log "3) removed $OVERLAY_CONF"; }
        [ -f "$REMOUNT_DROPIN" ] && { rm -f "$REMOUNT_DROPIN"; log "3) removed $REMOUNT_DROPIN"; }
        _strip_block "$CONFIG_TXT"
        # Drop the disable token too, so a clean revert leaves cmdline.txt as it was.
        sed -i 's/ *overlayroot=disabled//g' "$CMDLINE_TXT"
        update-initramfs -u >/dev/null 2>&1 || true
        log "3) overlay config removed (reboot to fully unlock)"
        return
    fi

    # recurse=0 is load-bearing: without it the overlay drives every other mount
    # (including our /var/lib/camtest loop) read-only too.
    _atomic_write "$OVERLAY_CONF" 'overlayroot="tmpfs:recurse=0"'$'\n'
    log "3) wrote $OVERLAY_CONF (tmpfs:recurse=0)"

    # Force the legacy mount API so systemd-remount-fs does not fail under the
    # overlay (Trixie/systemd #39558). Keeps the box out of 'degraded' state and
    # off the FAILED console lines.
    install -d -m 0755 "$(dirname "$REMOUNT_DROPIN")"
    _atomic_write "$REMOUNT_DROPIN" \
        '[Manager]'$'\n''DefaultEnvironment="LIBMOUNT_FORCE_MOUNT2=always"'$'\n'
    log "3) wrote $REMOUNT_DROPIN (legacy mount API for remount-fs)"

    # auto_initramfs=1 makes the firmware load the initramfs that carries the
    # overlay hook. Managed block so it is reversible.
    if ! grep -qF "$BEGIN" "$CONFIG_TXT"; then
        local block
        block="$(printf '%s\n%s\n%s' "$BEGIN" "auto_initramfs=1" "$END")"
        local kept
        kept="$(cat "$CONFIG_TXT")"
        _atomic_write "$CONFIG_TXT" "${kept%$'\n'}"$'\n\n'"${block}"$'\n'
        log "3) config.txt: enabled auto_initramfs"
    else
        log "3) config.txt: auto_initramfs block already present"
    fi

    # Stage the overlay DISABLED. The first post-install boot then comes up
    # writable so first-boot tasks settle; the one-shot finaliser removes this
    # token and reboots to bring the overlay up for real. Without this, the
    # overlay would engage on the very next boot with no writable settle-boot.
    if ! grep -q 'overlayroot=disabled' "$CMDLINE_TXT"; then
        sed -i 's/[[:space:]]*$/ overlayroot=disabled/' "$CMDLINE_TXT"
        log "3) cmdline.txt: staged overlay disabled (writable settle-boot)"
    else
        log "3) cmdline.txt: overlay-disabled token already present"
    fi

    update-initramfs -u >/dev/null
    log "3) refreshed initramfs"
}

# Stage 4: finaliser script + one-shot unit. This is what actually locks the box
# down, on the NEXT boot, after the writable settle-boot.
stage_finalise() {
    if [ "$REVERT" -eq 1 ]; then
        systemctl disable "$ONESHOT_UNIT" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$ONESHOT_UNIT" "$FINALISE_SCRIPT"
        systemctl daemon-reload 2>/dev/null || true
        log "4) removed finaliser + one-shot unit"
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

logger -t camtest-readonly "finaliser starting"

# Only act during the staged-but-disabled settle-boot. If the disable token is
# gone the overlay is already engaged (or was never staged) - nothing to do.
if ! grep -q 'overlayroot=disabled' "$CMDLINE"; then
    logger -t camtest-readonly "overlay not in disabled-settle state, nothing to do"
    systemctl disable camtest-readonly-firstboot.service >/dev/null 2>&1 || true
    exit 0
fi

# Refuse to lock down unless the persistent data mount is actually writable,
# otherwise the kiosk would lose its state with no way to write it.
if ! mountpoint -q /var/lib/camtest; then
    logger -t camtest-readonly "ABORT: /var/lib/camtest not mounted, leaving box writable"
    exit 1
fi
if ! touch /var/lib/camtest/.write-probe 2>/dev/null; then
    logger -t camtest-readonly "ABORT: /var/lib/camtest not writable, leaving box writable"
    exit 1
fi
rm -f /var/lib/camtest/.write-probe

# Clear any leftover maintenance disable token so the overlay engages next boot.
if grep -q 'overlayroot=disabled' "$CMDLINE"; then
    mount -o remount,rw "$FW_DIR" 2>/dev/null || true
    sed -i 's/ *overlayroot=disabled//g' "$CMDLINE"
    logger -t camtest-readonly "cleared overlayroot=disabled from cmdline"
fi

# Disable self BEFORE rebooting so a wedged overlay can never loop-reboot.
systemctl disable camtest-readonly-firstboot.service >/dev/null 2>&1 || true
logger -t camtest-readonly "locked in, rebooting into read-only root"
sync
systemctl reboot
FINEOF
    chmod 0755 "$FINALISE_SCRIPT"
    log "4) installed finaliser $FINALISE_SCRIPT"

    cp "$REPO_DIR/deploy/$ONESHOT_UNIT" "/etc/systemd/system/$ONESHOT_UNIT"
    systemctl daemon-reload
    systemctl enable "$ONESHOT_UNIT" >/dev/null 2>&1 || true
    log "4) armed $ONESHOT_UNIT (locks down on next boot)"
}

if [ "$REVERT" -eq 1 ]; then
    header "Read-only root - reverting all stages"
else
    header "Read-only root - staging (locks in on next boot)"
fi

stage_packages
stage_data
stage_overlay
stage_finalise

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to come up writable: sudo reboot"
else
    log "Staged. The next reboot settles first-boot tasks, then the finaliser"
    log "locks the root read-only and reboots once more, automatically."
    log "Dev toggle afterwards: camtestctl rw  /  camtestctl ro"
fi
