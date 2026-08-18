#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Boot splash via kernel fullscreen logo (no Plymouth).
# logo.tga from initramfs until Cage modesets. HDMI only for kernel path.
# DSI fbdev arrives late: udev runs camlab-splash@fbN with fbsplash.py.
# Regenerate logo.tga from splash.png with:
#   convert splash.png -background black -alpha remove -alpha off -colors 224 \
#     -depth 8 -type TrueColor -compress none logo.tga
# Safe to re-run on writable root. Requires sudo. Reboot to apply.
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
FBSPLASH_FONT="/usr/local/lib/camlab/Roboto-Regular.ttf"
FBSPLASH_UNIT="/etc/systemd/system/camlab-splash@.service"
FBSPLASH_RULE="/etc/udev/rules.d/99-camlab-splash.rules"

# Fullscreen logo cmdline tokens. boot.sh removes quiet/logo.nologo (suppress logo).
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
    if cmp -s "$SPLASH_SRC/logo.tga" "$LOGO_TGA" && cmp -s "$SPLASH_SRC/initramfs-hook" "$INITRAMFS_HOOK"; then
        log "logo.tga already in initramfs"
        return
    fi
    install -m 0644 "$SPLASH_SRC/logo.tga" "$LOGO_TGA"
    install -m 0755 "$SPLASH_SRC/initramfs-hook" "$INITRAMFS_HOOK"
    # Costs 4 s, so only when the logo or the hook actually moved.
    update-initramfs -u >/dev/null
    log "logo.tga bundled into initramfs"
}

stage_fbsplash() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$FBSPLASH_BIN" "$FBSPLASH_FONT" "$FBSPLASH_UNIT" "$FBSPLASH_RULE"
        systemctl daemon-reload
        udevadm control --reload-rules 2>/dev/null || true
        log "removed fbdev splash writer"
        return
    fi
    log "Stage: fbdev splash writer"
    install -D -m 0755 "$SPLASH_SRC/fbsplash.py" "$FBSPLASH_BIN"
    # Status line runs before any compositor, so it reads the face off disk.
    install -m 0644 "$REPO_DIR/camlab/assets/Roboto-Regular.ttf" "$FBSPLASH_FONT"
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
    log "Revert complete. Reboot to restore stock behavior."
else
    log "Done."
fi
