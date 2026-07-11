#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install Kurokesu out-of-tree sensor drivers, DKMS source packages from the
# Kurokesu apt archive (enabled by deps.sh). Package postinst compiles
# <sensor>.dtbo into /boot/overlays, which must be a symlink to the live
# /boot/firmware/overlays dir on Trixie.
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

# Driver packages come from the sensor registry (camlab/sensors.yaml), adding
# a sensor is a single edit there.
REPO_DIR="$(resolve_repo_dir)"
SENSORS_YAML="$REPO_DIR/camlab/sensors.yaml"

python3 -c 'import yaml' 2>/dev/null \
    || die "python3-yaml not installed (run scripts/setup/deps.sh first)"

declare -A DRIVER_PACKAGE=()
DEFAULT_SENSORS=()
while IFS=$'\t' read -r overlay package; do
    [ -n "$overlay" ] || continue
    DRIVER_PACKAGE["$overlay"]="$package"
    DEFAULT_SENSORS+=("$overlay")
done < <(python3 - "$SENSORS_YAML" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f) or {}
for s in (data.get("sensors") or []):
    overlay, package = s.get("overlay"), s.get("driver_package")
    if overlay and package:
        print(f"{overlay}\t{package}")
PY
)
[ "${#DRIVER_PACKAGE[@]}" -gt 0 ] || die "no sensors with a driver_package in $SENSORS_YAML"

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

# DKMS builds against the running kernel. If an apt upgrade installed a new
# kernel but the box has not rebooted, the modules we build now would not load
# after the next reboot. Use the canonical reboot-required flag (set by the
# kernel package post-install) rather than comparing /lib/modules entries, which
# carries several flavors (rpi-2712, rpi-v8) of the same version and would
# misfire on a CM5 running the 2712 flavor. The package list lives in the .pkgs
# companion; we only block when a kernel package is the reason.
if [ -f /run/reboot-required ] \
   && grep -qiE 'linux-image|raspi-firmware|rpi-.*kernel' /run/reboot-required.pkgs 2>/dev/null; then
    die "a kernel update is pending a reboot ($(uname -r) is running). Reboot first, then re-run."
fi

FW_OVERLAYS="/boot/firmware/overlays"

header "Installing sensor drivers: ${SENSORS[*]}"

# Trixie: drivers install overlays to /boot/overlays, but the active dir is
# /boot/firmware/overlays. Make /boot/overlays point there.
if [ -d "$FW_OVERLAYS" ] && [ ! -e /boot/overlays ]; then
    ln -s firmware/overlays /boot/overlays
    log "Symlinked /boot/overlays -> firmware/overlays"
elif [ -L /boot/overlays ]; then
    log "/boot/overlays already a symlink ($(readlink /boot/overlays))"
elif [ -d /boot/overlays ] && [ ! -L /boot/overlays ]; then
    warn "/boot/overlays is a real directory, not a symlink to $FW_OVERLAYS."
    warn "Driver overlays may land in the wrong place; verify after build."
fi

PACKAGES=()
for sensor in "${SENSORS[@]}"; do
    package="${DRIVER_PACKAGE[$sensor]:-}"
    [ -n "$package" ] || die "no driver package known for sensor '$sensor'"
    PACKAGES+=("$package")
done

log "Installing: ${PACKAGES[*]}"
apt_get install -y "${PACKAGES[@]}"

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
