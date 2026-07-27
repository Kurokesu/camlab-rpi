#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Boot splash via the kernel fullscreen logo (no daemon, no DRM, no Plymouth).
# The rpi kernel draws deploy/splash/logo.tga from the initramfs at fbcon
# init, the earliest point custom pixels appear, and it holds until Cage
# modesets over it. The Qt boot cover then carries a black screen until the
# first camera frame.
# kernel draw only reaches the firmware framebuffer, which scans out on
# HDMI. DSI panel fbdev registers seconds later, past the boot logo window,
# so a udev rule starts camlab-splash@fbN.service to repaint logo there
# with fbsplash.py.
# Regenerate logo.tga from splash.png with:
#   convert splash.png -background black -alpha remove -alpha off -colors 224 \
#     -depth 8 -type TrueColor -compress none logo.tga
# Safe to re-run on a writable root. Requires sudo. Reboot to apply.
#
# Usage:
#   sudo scripts/setup/splash.sh            # install + activate
#   sudo scripts/setup/splash.sh --revert   # undo everything
#   sudo scripts/setup/splash.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="splash"

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

REPO_DIR="$(resolve_repo_dir)"
SPLASH_SRC="$REPO_DIR/deploy/splash"
FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
CMDLINE_TXT="$FW_DIR/cmdline.txt"
LOGO_TGA="/lib/firmware/logo.tga"
INITRAMFS_HOOK="/etc/initramfs-tools/hooks/camlab-splash"
FBSPLASH_BIN="/usr/local/lib/camlab/fbsplash.py"
FBSPLASH_UNIT="/etc/systemd/system/camlab-splash@.service"
FBSPLASH_RULE="/etc/udev/rules.d/99-camlab-splash.rules"

# Kernel fullscreen-logo tokens. boot.sh owns the quiet-console tokens and
# already removes quiet/logo.nologo, which suppress the logo.
CMDLINE_TOKENS=(
    fullscreen_logo=1
    fullscreen_logo_name=logo.tga
)

stage_logo() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$LOGO_TGA" "$INITRAMFS_HOOK"
        update-initramfs -u >/dev/null 2>&1 || true
        log "removed boot logo and initramfs hook"
        return
    fi
    log "Stage: kernel boot logo"
    install -m 0644 "$SPLASH_SRC/logo.tga" "$LOGO_TGA"
    install -m 0755 "$SPLASH_SRC/initramfs-hook" "$INITRAMFS_HOOK"
    update-initramfs -u >/dev/null
    log "logo.tga bundled into initramfs"
}

stage_fbsplash() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$FBSPLASH_BIN" "$FBSPLASH_UNIT" "$FBSPLASH_RULE"
        systemctl daemon-reload
        udevadm control --reload-rules 2>/dev/null || true
        log "removed fbdev splash writer"
        return
    fi
    log "Stage: fbdev splash writer"
    install -D -m 0755 "$SPLASH_SRC/fbsplash.py" "$FBSPLASH_BIN"
    install -m 0644 "$SPLASH_SRC/camlab-splash@.service" "$FBSPLASH_UNIT"
    install -m 0644 "$SPLASH_SRC/99-camlab-splash.rules" "$FBSPLASH_RULE"
    systemctl daemon-reload
    udevadm control --reload-rules 2>/dev/null || true
    log "DRM fbdevs get the logo via camlab-splash@.service"
}

stage_cmdline() {
    local t
    [ -f "$CMDLINE_TXT" ] || { warn "$CMDLINE_TXT missing, skipping cmdline"; return; }
    if [ "$REVERT" -eq 1 ]; then
        for t in "${CMDLINE_TOKENS[@]}"; do cmdline_remove "$CMDLINE_TXT" "$t"; done
        log "cmdline: fullscreen logo tokens removed"
        return
    fi
    log "Stage: cmdline tokens"
    for t in "${CMDLINE_TOKENS[@]}"; do cmdline_add "$CMDLINE_TXT" "$t"; done
    log "cmdline: fullscreen logo enabled"
}

if [ "$REVERT" -eq 1 ]; then
    header "Boot splash - reverting"
else
    header "Boot splash - installing"
fi

stage_logo
stage_fbsplash
stage_cmdline

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to restore stock behaviour."
else
    log "Done."
fi
