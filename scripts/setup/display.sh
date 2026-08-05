#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# DSI touch panel overlay in /boot/firmware/config.txt via camlab.config_manager.
# CM5 needs this (firmware never auto-detects DSI). Pi 5 auto-detects, skip overlay.
# Safe to re-run. Requires sudo. Reboot to apply.
#
# Usage:
#   sudo scripts/setup/display.sh --overlay vc4-kms-dsi-7inch        # panel on DISP1
#   sudo scripts/setup/display.sh --overlay vc4-kms-dsi-7inch,dsi0   # panel on DISP0
#   sudo scripts/setup/display.sh --revert                           # drop the block
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
REPO_DIR="$(resolve_repo_dir)"

# Path override shared with config.txt scripts.
FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
export CAMLAB_CONFIG_TXT="$FW_DIR/config.txt"
export CAMLAB_OVERLAYS_DIR="$FW_DIR/overlays"

if [ "$REVERT" -eq 1 ]; then
    header "Display overlay - reverting"
    ( cd "$REPO_DIR" && python3 -m camlab.config_manager display-clear )
    log "config.txt: removed camlab display block"
    log "Revert complete. Reboot to apply."
    exit 0
fi

[ -n "$OVERLAY" ] || die "--overlay is required (or --revert to remove)"

header "Display overlay - installing ($OVERLAY)"

# display_auto_detect=0 breaks Pi 5 panel auto-detect. Warn likely misuse.
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
case "$MODEL" in
    *"Compute Module"*|"") ;;
    *) warn "$MODEL auto-detects DSI panels, --overlay is normally CM5-only" ;;
esac

( cd "$REPO_DIR" && python3 -m camlab.config_manager display-set --overlay "$OVERLAY" )
log "config.txt: wrote display block ($OVERLAY)"
log "Done."
