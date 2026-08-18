#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Keep the running kernel alone. Purge the flavor the board never boots (RPi OS
# ships both 2712 and v8, firmware picks by SoC) and the versions apt keeps as a
# fallback, which needs a boot menu a Pi has no equivalent of. Every extra kernel
# costs a DKMS rebuild of every sensor module.
# Trade-off: the media no longer boots other Pi family.
#
# Then hold what is left, so apt cannot swap the kernel under DKMS sensor
# modules, where a failed rebuild kills cameras on next boot. Lift only for a
# validated kernel.
# Safe to re-run (no-op once trimmed). Requires sudo.
#
# Usage:
#   sudo scripts/setup/kernel.sh            # keep the running kernel, then hold
#   sudo scripts/setup/kernel.sh --unhold   # lift the hold (validated kernel only)
#   sudo scripts/setup/kernel.sh --revert   # reinstall the sibling kernel
#   sudo scripts/setup/kernel.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="kernel"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

REVERT=0
UNHOLD=0
for arg in "$@"; do
    case "$arg" in
        --revert) REVERT=1 ;;
        --unhold) UNHOLD=1 ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

# Installed kernel packages: flavor metapackages plus versioned image and headers
# packages. Shared linux-headers-*-common-rpi matches too, holding it keeps a
# header tree from moving on its own.
kernel_packages() {
    dpkg-query -Wf '${db:Status-Status} ${Package}\n' \
            'linux-image-*' 'linux-headers-*' 2>/dev/null \
        | awk '$1 == "installed" { print $2 }'
}

# The sibling flavor at any release, plus releases older than the running one.
# A newer release stays: an upgrade without a reboot has already pointed firmware
# at its image. Flavor metapackages carry no release and stay.
doomed_packages() {
    local pkg release
    while read -r pkg; do
        case "$pkg" in
            *"rpi-$SIBLING") printf '%s\n' "$pkg"; continue ;;
            *+rpt*) ;;
            *) continue ;;
        esac
        release="${pkg#linux-image-}"
        release="${release#linux-headers-}"
        release="${release%-common-rpi}"
        release="${release%-rpi-*}"
        if dpkg --compare-versions "$release" lt "$RUNNING_VER"; then
            printf '%s\n' "$pkg"
        fi
    done < <(kernel_packages)
}

if [ "$UNHOLD" -eq 1 ]; then
    header "Kernel hold - lifting"
    mapfile -t HELD < <(apt-mark showhold | grep -E '^linux-(image|headers)-' || true)
    if [ "${#HELD[@]}" -eq 0 ]; then
        log "No kernel packages on hold. Nothing to do."
        exit 0
    fi
    apt-mark unhold "${HELD[@]}" >/dev/null
    log "Unheld: ${HELD[*]}"
    warn "Next apt upgrade can replace the kernel and rebuild every DKMS sensor"
    warn "module. Verify cameras after, then re-run this script to hold."
    exit 0
fi

RUNNING="$(uname -r)"  # e.g. 6.18.34+rpt-rpi-2712
case "$RUNNING" in
    *+rpt-rpi-*) FLAVOR="${RUNNING##*+rpt-rpi-}" ;;
    *) die "unexpected kernel release '$RUNNING' (want *+rpt-rpi-<flavor>)" ;;
esac
RUNNING_VER="${RUNNING%-rpi-*}"  # e.g. 6.18.34+rpt, shared by image and headers names

# RPi OS arm64 ships exactly two flavors.
case "$FLAVOR" in
    2712) SIBLING="v8" ;;
    v8)   SIBLING="2712" ;;
    *) die "unknown kernel flavor '$FLAVOR' (expected 2712 or v8)" ;;
esac

if [ "$REVERT" -eq 1 ]; then
    header "Kernel trim - reinstalling sibling flavor ($SIBLING)"
    apt_get install -y "linux-image-rpi-$SIBLING" "linux-headers-rpi-$SIBLING"
    log "Done. Sibling kernel restored (universal-image behavior is back)."
    exit 0
fi

header "Kernel trim - keeping $RUNNING alone"

mapfile -t DOOMED < <(doomed_packages)

# Hard guard: never remove what the running kernel needs, whatever matched above.
KEEP=(
    "linux-image-rpi-$FLAVOR"
    "linux-headers-rpi-$FLAVOR"
    "linux-image-$RUNNING"
    "linux-headers-$RUNNING"
    "linux-headers-$RUNNING_VER-common-rpi"
)

if [ "${#DOOMED[@]}" -eq 0 ]; then
    log "Only the running kernel is installed, nothing to purge."
else
    for pkg in "${DOOMED[@]}"; do
        case " ${KEEP[*]} " in
            *" $pkg "*) die "refusing to remove '$pkg' (running $RUNNING)" ;;
        esac
    done

    log "Purging: ${DOOMED[*]}"
    # An earlier run held the kernel it ran under, which a reboot makes stale.
    apt-mark unhold "${DOOMED[@]}" >/dev/null
    # Separate autoremove: one pass misses linux-base-<ver>, which orphans
    # only once linux-base-rpi-<flavor> is gone.
    apt_get purge -y "${DOOMED[@]}"
    apt_get autoremove --purge -y
    log "DKMS now builds for $RUNNING only."
fi

header "Kernel hold"

# Hold after the purge, so packages on their way out are never held first.
mapfile -t KEPT < <(kernel_packages)
if [ "${#KEPT[@]}" -eq 0 ]; then
    warn "No installed kernel packages found, nothing held. Check 'dpkg -l linux-image-*'."
else
    apt-mark hold "${KEPT[@]}" >/dev/null
    log "Held: ${KEPT[*]}"
    log "Lift for a validated kernel with: sudo scripts/setup/kernel.sh --unhold"
fi
