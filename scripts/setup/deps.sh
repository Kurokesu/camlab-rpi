#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Install camlab APT dependencies: the Kurokesu libcamera/rpicam-apps fork, the
# Python preview/GUI stack (picamera2 + PyQt5 + OpenGL), Cage, and DKMS build
# tools. Run archive.sh first so the +krks packages are available/preferred.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/deps.sh

set -euo pipefail

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

log "Updating package lists..."
apt-get update

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

# DKMS + toolchain for the out-of-tree sensor drivers. --no-install-recommends
# keeps dkms from pulling a large recommended set we don't need on a bench box.
log "Installing DKMS + build tools..."
apt-get install -y --no-install-recommends dkms
apt-get install -y git build-essential device-tree-compiler

log "Done. All apt dependencies installed."
