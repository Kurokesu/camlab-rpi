#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Boot splash via the kernel fullscreen logo (no daemon, no DRM, no Plymouth).
# The rpi kernel draws deploy/splash/logo.tga from the initramfs at fbcon
# init, the earliest point custom pixels appear, and it holds until Cage
# modesets over it. The Qt boot cover then carries a black screen until the
# first camera frame.
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
stage_cmdline

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to restore stock behaviour."
else
    log "Done."
fi
