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

# Official archive setup: signing key, deb822 source, apt refresh.
log "Enabling Kurokesu apt archive..."
ARCHIVE_SETUP="$(mktemp)"
curl -fsSL https://apt.kurokesu.com/setup.sh -o "$ARCHIVE_SETUP"
sh "$ARCHIVE_SETUP" --update
rm -f "$ARCHIVE_SETUP"

# eatmydata first (plain apt-get) so apt_get can use it below.
log "Installing eatmydata..."
apt-get install -y eatmydata

# One pass, recommends off. picamera2 pulls +krks libcamera fork.
# Pinned recommends: python3-opengl, qt6-wayland, awb-nn. wlr-randr for HDMI/DSI switch.
# python3-pil and fonts-dejavu-core draw the boot splash text.
log "Installing packages..."
apt_get install -y --no-install-recommends \
    python3-picamera2 \
    python3-pyqt6 python3-opengl \
    python3-yaml python3-pil \
    cage wlr-randr fonts-dejavu-core \
    qt6-wayland awb-nn

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
