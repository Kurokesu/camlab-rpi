#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
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

# Official archive setup script. It installs the signing key, verifies its
# fingerprint, writes deb822 source and refreshes apt (--update).
log "Enabling Kurokesu apt archive..."
ARCHIVE_SETUP="$(mktemp)"
curl -fsSL https://apt.kurokesu.com/setup.sh -o "$ARCHIVE_SETUP"
sh "$ARCHIVE_SETUP" --update
rm -f "$ARCHIVE_SETUP"

# eatmydata first (plain apt-get) so apt_get can use it below.
log "Installing eatmydata..."
apt-get install -y eatmydata

# One resolver pass, recommends off to keep GUI extras (VA/VDPAU/Vulkan, GTK,
# QML) away. Hard deps stay unlisted: picamera2 pulls the +krks libcamera
# fork. Recommends the kiosk does need are pinned: python3-opengl
# (QGl6Picamera2 imports it), qt6-wayland (Qt under Cage), awb-nn
# (libcamera-ipa AWB models).
log "Installing packages..."
apt_get install -y --no-install-recommends \
    python3-picamera2 \
    python3-pyqt6 python3-opengl \
    python3-yaml \
    cage \
    qt6-wayland awb-nn

# camlab never runs rpicam-* CLI. Drop preinstalled rpicam-apps stack
# and its OpenCV deps (~20 MB). picamera2 keeps libcamera from autoremoval.
# Purge only what is actually installed: hardcoded names would abort the
# install (set -e) if a name ever disappears from configured sources.
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
