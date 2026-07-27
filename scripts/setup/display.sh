#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# DSI touch panel overlay: writes the camlab display block in
# /boot/firmware/config.txt via camlab.config_manager, the same code path
# the GUI uses. Needed on CM5, where firmware never auto-detects DSI panels.
# Pi 5 detects supported panels on its own and needs none of this.
# Safe to re-run. Requires sudo. Changes take hold after a reboot.
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

# Honor the test override used by the other config.txt scripts.
FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
export CAMLAB_CONFIG_TXT="$FW_DIR/config.txt"
export CAMLAB_OVERLAYS_DIR="$FW_DIR/overlays"

if [ "$REVERT" -eq 1 ]; then
    ( cd "$REPO_DIR" && python3 -m camlab.config_manager display-clear )
    log "config.txt: removed camlab display block"
    exit 0
fi

[ -n "$OVERLAY" ] || die "--overlay is required (or --revert to remove)"

# Our block sets display_auto_detect=0, costing Pi 5 its panel auto-detect,
# so flag likely misuse.
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
case "$MODEL" in
    *"Compute Module"*|"") ;;
    *) warn "$MODEL auto-detects DSI panels, --overlay is normally CM5-only" ;;
esac

( cd "$REPO_DIR" && python3 -m camlab.config_manager display-set --overlay "$OVERLAY" )
log "config.txt: wrote display block ($OVERLAY)"
