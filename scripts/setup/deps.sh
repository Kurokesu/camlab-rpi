#!/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install camlab APT dependencies: Kurokesu apt archive then everything
# apt-packages lists, with the pins that file declares.
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

# Same path the updater keys provenance off
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

# eatmydata first (plain apt-get) so apt_get can use it below
if ! command -v eatmydata >/dev/null 2>&1; then
    log "Installing eatmydata..."
    apt-get install -y eatmydata
fi

# Deb holds floors and app packages as Depends, leave them to apt
if [ -z "$(missing_packages camlab-rpi)" ]; then
    log "Done. Dependencies came with camlab-rpi."
    exit 0
fi

REPO="$(resolve_repo_dir)"

# shellcheck source=../../apt-packages
source "$REPO/apt-packages"

STACK=("$LIBCAMERA_VERSION $LIBCAMERA_PACKAGES"
       "$RPICAM_APPS_VERSION $RPICAM_APPS_PACKAGES")
FLOORED=("${STACK[@]}" "$CAGE_VERSION $CAGE_PACKAGES")

# Raising a floor can pull a new package, which plain upgrade refuses to do
BELOW=()
for pin in "${FLOORED[@]}"; do
    read -r floor packages <<<"$pin"
    for pkg in $packages; do
        have="$(dpkg-query -Wf '${Version}' "$pkg" 2>/dev/null)" || have=""
        if dpkg --compare-versions "${have:-0}" lt "$floor"; then
            BELOW+=("$pkg")
        fi
    done
done

if [ "${#BELOW[@]}" -gt 0 ]; then
    log "Raising to floor: ${BELOW[*]}"
    mapfile -t FLOORS < <(floor_relations "${FLOORED[@]}")
    apt_get satisfy -y --no-install-recommends "${FLOORS[@]}"
else
    log "Floored packages already meet the floor."
fi

AHEAD=()
for pin in "${STACK[@]}"; do
    read -r floor packages <<<"$pin"
    for pkg in $packages; do
        have="$(dpkg-query -Wf '${Version}' "$pkg")"
        if dpkg --compare-versions "$have" ge "$floor."; then
            AHEAD+=("$pkg=$have")
        fi
    done
done

if [ "${#AHEAD[@]}" -gt 0 ]; then
    warn "Camera stack newer than validated in apt-packages: ${AHEAD[*]}"
fi

# shellcheck disable=SC2206  # test_stack_pin.py asserts names are glob-free
UNPINNED=($APP_PACKAGES $PICAMERA2_PACKAGES)

mapfile -t MISSING < <(missing_packages "${UNPINNED[@]}")

if [ "${#MISSING[@]}" -gt 0 ]; then
    log "Installing packages: ${MISSING[*]}"
    apt_get install -y --no-install-recommends "${MISSING[@]}"
else
    log "Packages already installed."
fi

# Mark manual, or autoremove reclaims what a removed package left auto
# shellcheck disable=SC2086  # test_stack_pin.py asserts names are glob-free
apt-mark manual "${UNPINNED[@]}" $CAGE_PACKAGES >/dev/null

log "Done. All apt dependencies installed."
