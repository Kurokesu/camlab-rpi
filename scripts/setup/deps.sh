#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install camlab APT dependencies: Kurokesu apt archive, Kurokesu libcamera
# fork, Python preview/GUI stack (picamera2 + PyQt6 + OpenGL) and Cage.
# Also removes preinstalled rpicam-apps stack camlab never uses.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/deps.sh

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="deps"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

header "Installing camlab apt dependencies"

# Same path the updater keys provenance off.
ARCHIVE_SOURCES="/etc/apt/sources.list.d/kurokesu.sources"
ARCHIVE_KEYRING="/etc/apt/keyrings/kurokesu-archive-keyring.gpg"

# Installing from apt already enabled it, and a refresh costs a full index fetch.
enable_archive() {
    if [ ! -f "$ARCHIVE_SOURCES" ] || [ ! -f "$ARCHIVE_KEYRING" ]; then
        log "Enabling Kurokesu apt archive..."
        local setup
        setup="$(mktemp)"
        curl -fsSL https://apt.kurokesu.com/setup.sh -o "$setup"
        sh "$setup" --update
        rm -f "$setup"
        return
    fi

    # Enabled but never fetched, so apt cannot see the packages yet.
    local lists=(/var/lib/apt/lists/apt.kurokesu.com_*_Packages*)
    if [ ! -e "${lists[0]}" ]; then
        log "Kurokesu apt archive enabled, fetching index..."
        apt_get update
        return
    fi

    log "Kurokesu apt archive already enabled."
}

enable_archive

# eatmydata first (plain apt-get) so apt_get can use it below.
if ! command -v eatmydata >/dev/null 2>&1; then
    log "Installing eatmydata..."
    apt-get install -y eatmydata
fi

# One pass, recommends off. picamera2 pulls +krks libcamera fork.
# Pinned recommends: python3-opengl, qt6-wayland, awb-nn. wlr-randr for HDMI/DSI switch.
# python3-pil draws boot splash text.
mapfile -t MISSING < <(missing_packages \
    python3-picamera2 \
    python3-pyqt6 python3-opengl \
    python3-yaml python3-pil \
    cage wlr-randr \
    qt6-wayland awb-nn)

if [ "${#MISSING[@]}" -gt 0 ]; then
    log "Installing packages: ${MISSING[*]}"
    apt_get install -y --no-install-recommends "${MISSING[@]}"
else
    log "Packages already installed."
fi

# camlab never runs rpicam-* CLI. Purge only installed names (set -e safe).
log "Removing unused rpicam-apps stack..."
mapfile -t RPICAM < <(dpkg-query -Wf '${db:Status-Status} ${Package}\n' 'rpicam-apps*' 2>/dev/null \
    | awk '$1 == "installed" { print $2 }')
if [ "${#RPICAM[@]}" -gt 0 ]; then
    apt_get purge -y "${RPICAM[@]}"
else
    log "No rpicam-apps packages installed."
fi
apt_get autoremove --purge -y

log "Done. All apt dependencies installed."
