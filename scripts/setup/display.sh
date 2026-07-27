#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# DSI touch panel overlay: owns a managed block in /boot/firmware/config.txt.
# Needed on CM5, where firmware never auto-detects DSI panels. Pi 5 detects
# supported panels on its own and needs none of this.
# Safe to re-run. Requires sudo. Changes take hold after a reboot.
#
# Usage:
#   sudo scripts/setup/display.sh --overlay vc4-kms-dsi-7inch   # enable panel
#   sudo scripts/setup/display.sh --revert                      # drop the block
#   sudo scripts/setup/display.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="display"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

OVERLAY=""
REVERT=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --overlay) OVERLAY="${2:?--overlay needs an overlay name}"; shift 2 ;;
        --revert) REVERT=1; shift ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_root

FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
CONFIG_TXT="$FW_DIR/config.txt"

BEGIN="# >>> camlab display (do not edit) >>>"
END="# <<< camlab display <<<"

[ -f "$CONFIG_TXT" ] || die "$CONFIG_TXT missing"

if [ "$REVERT" -eq 1 ]; then
    block_strip "$CONFIG_TXT" "$BEGIN" "$END"
    log "config.txt: removed camlab display block"
    exit 0
fi

[ -n "$OVERLAY" ] || die "--overlay is required (or --revert to remove)"

# display_auto_detect=0: the explicit overlay owns the panel, keep Pi 5
# firmware from loading it a second time.
block_write "$CONFIG_TXT" "$BEGIN" "$END" \
    $'display_auto_detect=0\n'"dtoverlay=${OVERLAY}"
log "config.txt: wrote display block ($OVERLAY)"
