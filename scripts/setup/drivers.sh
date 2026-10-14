#!/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install Kurokesu out-of-tree sensor drivers, DKMS source packages from the
# Kurokesu apt archive (enabled by deps.sh). Package postinst compiles
# <sensor>.dtbo into /boot/overlays.
# Safe to re-run. Requires sudo.
#
# Usage:
#   sudo scripts/setup/drivers.sh                 # install the default set
#   sudo scripts/setup/drivers.sh ar0822 ar0234   # install specific sensors

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="drivers"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

# Driver packages come from camlab/data/sensors.yaml, adding a sensor is a single
# edit there
REPO_DIR="$(resolve_repo_dir)"

python3 -c 'import yaml' 2>/dev/null \
    || die "python3-yaml not installed (run scripts/setup/deps.sh first)"

declare -A DRIVER_PACKAGE=()
DEFAULT_SENSORS=()
while IFS=$'\t' read -r overlay package; do
    DRIVER_PACKAGE["$overlay"]="$package"
    DEFAULT_SENSORS+=("$overlay")
done < <(cd "$REPO_DIR" && python3 -m camlab.sensors)
[ "${#DRIVER_PACKAGE[@]}" -gt 0 ] || die "no sensors with a driver_package in camlab/data/sensors.yaml"

SENSORS=()
for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        -*) die "Unknown argument: $arg" ;;
        *) SENSORS+=("$arg") ;;
    esac
done
[ "${#SENSORS[@]}" -gt 0 ] || SENSORS=("${DEFAULT_SENSORS[@]}")

require_root

FW_OVERLAYS="/boot/firmware/overlays"

header "Sensor drivers: ${SENSORS[*]}"

PACKAGES=()
for sensor in "${SENSORS[@]}"; do
    package="${DRIVER_PACKAGE[$sensor]:-}"
    [ -n "$package" ] || die "no driver package known for sensor '$sensor'"
    PACKAGES+=("$package")
done

# Naming a driver to apt would mark it manual
if [ -z "$(missing_packages camlab-rpi)" ]; then
    log "Drivers came with camlab-rpi."
else
    # Flag rather than /lib/modules, which carries several kernel flavors per version
    if [ -f /run/reboot-required ] \
       && grep -qiE 'linux-image|raspi-firmware|rpi-.*kernel' /run/reboot-required.pkgs 2>/dev/null; then
        die "a kernel update is pending a reboot ($(uname -r) is running). Reboot first, then re-run."
    fi

    # dkms only recommends gcc and recommends are off here
    mapfile -t MISSING < <(missing_packages gcc)
    # Drivers every run, so an outdated one upgrades
    apt_get install -y "${MISSING[@]}" "${PACKAGES[@]}"
fi

for sensor in "${SENSORS[@]}"; do
    if [ -f "$FW_OVERLAYS/${sensor}.dtbo" ]; then
        log "Overlay installed: $FW_OVERLAYS/${sensor}.dtbo"
    else
        warn "Expected $FW_OVERLAYS/${sensor}.dtbo not found - check the package postinst output."
    fi
done

log "dkms status:"
dkms status || true
log "Done."
