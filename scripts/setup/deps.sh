#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install camlab APT dependencies: Kurokesu apt archive then everything
# listed in apt-packages, camera stack pinned and the rest by presence.
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

# Deb holds stack range and app packages as Depends, leave them to apt
if [ -z "$(missing_packages camlab-rpi)" ]; then
    log "Done. Dependencies came with camlab-rpi."
    exit 0
fi

REPO="$(resolve_repo_dir)"

# shellcheck source=../../apt-packages
source "$REPO/apt-packages"

PINNED=("$LIBCAMERA_VERSION $LIBCAMERA_PACKAGES"
        "$RPICAM_APPS_VERSION $RPICAM_APPS_PACKAGES")

mapfile -t RELATIONS < <(stack_relations "${PINNED[@]}")

STALE=()
AHEAD=()
for pin in "${PINNED[@]}"; do
    read -r floor packages <<<"$pin"
    for pkg in $packages; do
        have="$(dpkg-query -Wf '${Version}' "$pkg" 2>/dev/null)" || have=""
        if dpkg --compare-versions "${have:-0}" lt "$floor"; then
            STALE+=("$pkg")
        elif dpkg --compare-versions "$have" ge "$floor."; then
            AHEAD+=("$pkg")
        fi
    done
done

if [ "${#AHEAD[@]}" -gt 0 ]; then
    # Downgrading would drop rpicam-apps, which needs matching libcamera ABI
    warn "Camera stack ahead of pin, left alone: ${AHEAD[*]}"
    warn "Pin is $LIBCAMERA_VERSION / $RPICAM_APPS_VERSION in apt-packages"
elif [ "${#STALE[@]}" -gt 0 ]; then
    log "Pinning camera stack: ${STALE[*]}"
    apt_get satisfy -y --no-install-recommends "${RELATIONS[@]}"
else
    log "Camera stack already matches the pin."
fi

# After the stack, or picamera2 pulls apt's candidate bindings past the pin
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
apt-mark manual "${UNPINNED[@]}"

log "Done. All apt dependencies installed."
