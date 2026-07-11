#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Purge the kernel flavor the board never boots (RPi OS ships both 2712 and
# v8. Firmware picks by SoC). Detected from running kernel, so it works
# on either family. Saves ~38 MB and halves DKMS sensor-module build time.
# Trade-off: the media no longer boots other Pi family.
# Safe to re-run (no-op once the sibling is gone). Requires sudo.
#
# Usage:
#   sudo scripts/setup/kernel.sh            # purge the sibling kernel flavor
#   sudo scripts/setup/kernel.sh --revert   # reinstall the sibling kernel
#   sudo scripts/setup/kernel.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="kernel"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

REVERT=0
for arg in "$@"; do
    case "$arg" in
        --revert) REVERT=1 ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

RUNNING="$(uname -r)"  # e.g. 6.18.34+rpt-rpi-2712
case "$RUNNING" in
    *+rpt-rpi-*) FLAVOR="${RUNNING##*+rpt-rpi-}" ;;
    *) die "unexpected kernel release '$RUNNING' (want *+rpt-rpi-<flavor>)" ;;
esac

# RPi OS arm64 ships exactly two flavors.
case "$FLAVOR" in
    2712) SIBLING="v8" ;;
    v8)   SIBLING="2712" ;;
    *) die "unknown kernel flavor '$FLAVOR' (expected 2712 or v8)" ;;
esac

if [ "$REVERT" -eq 1 ]; then
    header "Kernel trim - reinstalling sibling flavor ($SIBLING)"
    apt_get install -y "linux-image-rpi-$SIBLING" "linux-headers-rpi-$SIBLING"
    log "Done. Sibling kernel restored (universal-image behaviour is back)."
    exit 0
fi

header "Kernel trim - purging sibling flavor ($SIBLING, running $FLAVOR)"

# Metapackages and versioned image/headers packages all end in "rpi-<flavor>"
# (e.g. linux-image-rpi-v8, linux-image-6.18.34+rpt-rpi-v8). The shared
# linux-headers-*-common-rpi package matches neither suffix and is kept.
mapfile -t DOOMED < <(dpkg-query -Wf '${db:Status-Status} ${Package}\n' \
        'linux-image-*' 'linux-headers-*' 2>/dev/null \
    | awk -v suffix="rpi-$SIBLING" '$1 == "installed" && $2 ~ suffix"$" { print $2 }')

if [ "${#DOOMED[@]}" -eq 0 ]; then
    log "No $SIBLING kernel packages installed. Nothing to do."
    exit 0
fi

# Hard guard: never touch the running flavor, whatever the match above did.
for pkg in "${DOOMED[@]}"; do
    case "$pkg" in
        *"rpi-$FLAVOR"*) die "refusing to remove '$pkg' (matches running flavor $FLAVOR)" ;;
    esac
done

log "Purging: ${DOOMED[*]}"
apt_get purge -y "${DOOMED[@]}"
apt_get autoremove --purge -y

log "Done. DKMS now builds for the $FLAVOR kernel only."
