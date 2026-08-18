#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Copy source tree to /opt/camlab and precompile bytecode. Stages then swaps,
# so a failed copy cannot leave a half tree. Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/app-deploy.sh
#        sudo scripts/setup/app-deploy.sh && camlabctl restart   # dev loop

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="app-deploy"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

REPO_DIR="$(resolve_repo_dir)"
APP_DIR="/opt/camlab"

# Dev-clone clutter to prune from the copy
mapfile -t DEV_CLUTTER < <(awk '$2 == "export-ignore" { sub(/\/$/, "", $1); print $1 }' \
    "$REPO_DIR/.gitattributes" 2>/dev/null)
DEV_CLUTTER+=(.git .venv)

# Re-run from $APP_DIR skips the copy. Stage then swap, on failure old tree stays.
if [ "$REPO_DIR" != "$APP_DIR" ]; then
    header "Installing app to $APP_DIR"
    STAGE_DIR="$APP_DIR.new"
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"
    cp -a "$REPO_DIR/." "$STAGE_DIR/"
    for item in "${DEV_CLUTTER[@]}"; do
        rm -rf "${STAGE_DIR:?}/$item"
    done
    find "$STAGE_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
    chown -R root:root "$STAGE_DIR"
    rm -rf "$APP_DIR"
    mv "$STAGE_DIR" "$APP_DIR"
    log "Copied $REPO_DIR -> $APP_DIR"
fi
# Precompile: service user cannot write bytecode into root-owned tree.
python3 -m compileall -q -j 0 "$APP_DIR/camlab"
