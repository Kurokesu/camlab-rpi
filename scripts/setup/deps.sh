#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install camlab APT dependencies: Kurokesu apt archive, camera stack pinned by
# camera-stack.version, Python preview/GUI stack (picamera2 + PyQt6 + OpenGL)
# and Cage.
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

enable_archive() {
    if [ ! -f "$ARCHIVE_SOURCES" ] || [ ! -f "$ARCHIVE_KEYRING" ]; then
        log "Enabling Kurokesu apt archive..."
        local setup
        setup="$(mktemp)"
        curl -fsSL https://apt.kurokesu.com/setup.sh -o "$setup"
        sh "$setup"
        rm -f "$setup"
        return
    fi

    log "Kurokesu apt archive already enabled."
}

enable_archive

# Refresh every run
apt_get update

# eatmydata first (plain apt-get) so apt_get can use it below.
if ! command -v eatmydata >/dev/null 2>&1; then
    log "Installing eatmydata..."
    apt-get install -y eatmydata
fi

# Deb holds stack range and app packages as Depends, leave them to apt
if [ -z "$(missing_packages camlab-rpi)" ]; then
    log "Done. Dependencies came with camlab-rpi."
    exit 0
fi

# Stack first, or picamera2 pulls apt's candidate bindings past the pin
# shellcheck source=../../camera-stack.version
source "$(resolve_repo_dir)/camera-stack.version"

in_range() { dpkg --compare-versions "$1" ge "$2" && dpkg --compare-versions "$1" lt "$2."; }

RELATIONS=()
STALE=()
for pin in "$LIBCAMERA_VERSION $LIBCAMERA_PACKAGES" \
           "$RPICAM_APPS_VERSION $RPICAM_APPS_PACKAGES"; do
    read -r floor packages <<<"$pin"
    for pkg in $packages; do
        RELATIONS+=("$pkg (>= $floor)" "$pkg (<< $floor.)")
        have="$(dpkg-query -Wf '${Version}' "$pkg" 2>/dev/null)" || have=""
        in_range "${have:-0}" "$floor" || STALE+=("$pkg")
    done
done

if [ "${#STALE[@]}" -gt 0 ]; then
    log "Pinning camera stack: ${STALE[*]}"
    apt_get satisfy -y --no-install-recommends "${RELATIONS[@]}"
else
    log "Camera stack already matches the pin."
fi

# One pass, recommends off.
APP_PACKAGES=(
    awb-nn
    cage
    python3-opengl
    python3-picamera2
    python3-pil
    python3-pyqt6
    python3-yaml
    qt6-wayland
    wlr-randr
)

mapfile -t MISSING < <(missing_packages "${APP_PACKAGES[@]}")

if [ "${#MISSING[@]}" -gt 0 ]; then
    log "Installing packages: ${MISSING[*]}"
    apt_get install -y --no-install-recommends "${MISSING[@]}"
else
    log "Packages already installed."
fi

# Mark manual, or autoremove reclaims what a removed package left auto
apt-mark manual "${APP_PACKAGES[@]}"

log "Done. All apt dependencies installed."
