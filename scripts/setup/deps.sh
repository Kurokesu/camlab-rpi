#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Install camlab APT dependencies: Kurokesu apt archive, Kurokesu
# libcamera/rpicam-apps fork, Python preview/GUI stack (picamera2 + PyQt5 +
# OpenGL) and Cage.
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
# fork, qtopengl pulls base pyqt5. Recommends the kiosk does need are pinned:
# python3-opengl (QGlPicamera2 imports it), qtwayland5 (Qt under Cage),
# awb-nn (libcamera-ipa AWB models).
log "Installing packages..."
apt_get install -y --no-install-recommends \
    python3-picamera2 \
    python3-pyqt5.qtopengl python3-opengl \
    python3-yaml \
    cage \
    qtwayland5 awb-nn

# camlab never runs rpicam-* CLI. Drop the preinstalled rpicam-apps stack
# and its OpenCV deps (~20 MB). picamera2 keeps libcamera from autoremoval.
log "Removing unused rpicam-apps stack..."
apt_get purge -y rpicam-apps rpicam-apps-lite rpicam-apps-core \
    rpicam-apps-encoder rpicam-apps-opencv-postprocess rpicam-apps-preview
apt_get autoremove --purge -y

log "Done. All apt dependencies installed."
