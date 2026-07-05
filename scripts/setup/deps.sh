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

# Official archive setup script: installs the signing key (fingerprint
# verified), writes the deb822 source and refreshes apt (--update).
log "Enabling Kurokesu apt archive..."
ARCHIVE_SETUP="$(mktemp)"
curl -fsSL https://apt.kurokesu.com/setup.sh -o "$ARCHIVE_SETUP"
sh "$ARCHIVE_SETUP" --update
rm -f "$ARCHIVE_SETUP"

# Kurokesu libcamera/rpicam-apps fork (epoch-forced +krks). Pulls
# libcamera0.7 / libcamera-ipa / python3-libcamera as dependencies.
log "Installing Kurokesu libcamera + rpicam-apps fork..."
apt-get install -y rpicam-apps python3-libcamera

# Preview + GUI stack. picamera2 recommends pyqt5 + python3-opengl for the GL
# preview widget (QGlPicamera2). We install them explicitly.
log "Installing Python preview/GUI stack..."
apt-get install -y \
    python3-picamera2 \
    python3-pyqt5 python3-pyqt5.qtopengl python3-opengl \
    python3-yaml python3-smbus2

# Kiosk compositor.
log "Installing Cage kiosk compositor..."
apt-get install -y cage

log "Done. All apt dependencies installed."
