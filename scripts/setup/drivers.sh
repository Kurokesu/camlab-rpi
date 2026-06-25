#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Build + install the Kurokesu out-of-tree sensor drivers via DKMS and compile
# their device-tree overlays. Each driver repo ships a setup.sh (dkms add/build/
# install) and a dkms.postinst that compiles <sensor>.dtbo and installs it to
# /boot/overlays. On Trixie the live overlays live in /boot/firmware/overlays,
# so we ensure /boot/overlays is a symlink to it first.
# Safe to re-run. Requires sudo.
#
# Usage:
#   sudo scripts/setup/drivers.sh                 # build the default set
#   sudo scripts/setup/drivers.sh ar0822 ar0234   # build specific sensors

set -euo pipefail

CAMTEST_TAG="drivers"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

# Driver repos come from the sensor registry (camtest/sensors.yaml) so adding a
# sensor is a single edit there. Each entry's driver_repo is a repo name under
# the Kurokesu org (a full git URL is also accepted). Override the org base with
# CAMTEST_DRIVER_BASE_URL if a driver lives elsewhere.
REPO_DIR="$(resolve_repo_dir)"
SENSORS_YAML="$REPO_DIR/camtest/sensors.yaml"
DRIVER_BASE_URL="${CAMTEST_DRIVER_BASE_URL:-https://github.com/Kurokesu}"

python3 -c 'import yaml' 2>/dev/null \
    || die "python3-yaml not installed (run scripts/setup/deps.sh first)"

declare -A DRIVER_REPO=()
DEFAULT_SENSORS=()
while IFS=$'\t' read -r overlay repo; do
    [ -n "$overlay" ] || continue
    case "$repo" in
        http://*|https://*|git@*) url="$repo" ;;            # already a full URL
        *) url="$DRIVER_BASE_URL/${repo%.git}.git" ;;       # repo name under the org
    esac
    DRIVER_REPO["$overlay"]="$url"
    DEFAULT_SENSORS+=("$overlay")
done < <(python3 - "$SENSORS_YAML" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f) or {}
for s in (data.get("sensors") or []):
    overlay, repo = s.get("overlay"), s.get("driver_repo")
    if overlay and repo:
        print(f"{overlay}\t{repo}")
PY
)
[ "${#DRIVER_REPO[@]}" -gt 0 ] || die "no sensors with a driver_repo in $SENSORS_YAML"

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

# DKMS builds against the running kernel. If an apt upgrade installed a newer
# kernel but the box has not rebooted, uname -r is still the old one, so the
# modules we build now would not load after the next reboot. Refuse to build
# until the box is on the latest kernel. The newest /lib/modules entry is the
# latest installed kernel.
running_kernel="$(uname -r)"
latest_kernel="$(ls -1 /lib/modules 2>/dev/null | sort -V | tail -1)"
if [ -n "$latest_kernel" ] && [ "$latest_kernel" != "$running_kernel" ]; then
    die "running kernel ($running_kernel) is not the latest installed ($latest_kernel). Reboot first, then re-run."
fi

FW_OVERLAYS="/boot/firmware/overlays"
BUILD_ROOT="$CAMTEST_HOME/kurokesu-drivers"

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

install -d -o "$CAMTEST_USER" -g "$CAMTEST_USER" "$BUILD_ROOT"

for sensor in "${SENSORS[@]}"; do
    repo="${DRIVER_REPO[$sensor]:-}"
    [ -n "$repo" ] || die "no driver repo known for sensor '$sensor'"
    dir="$BUILD_ROOT/${sensor}-rpi-driver"

    header "Driver: $sensor"
    if [ -d "$dir/.git" ]; then
        log "Updating $dir"
        sudo -u "$CAMTEST_USER" git -C "$dir" pull --ff-only || warn "git pull failed; using existing checkout"
    else
        log "Cloning $repo"
        sudo -u "$CAMTEST_USER" git clone "$repo" "$dir"
    fi

    log "Running $sensor setup.sh (dkms add/build/install)"
    ( cd "$dir" && ./setup.sh )

    if [ -f "$FW_OVERLAYS/${sensor}.dtbo" ]; then
        log "Overlay installed: $FW_OVERLAYS/${sensor}.dtbo"
    else
        warn "Expected $FW_OVERLAYS/${sensor}.dtbo not found - check the build output."
    fi
done

log "dkms status:"
dkms status || true
log "Done."
