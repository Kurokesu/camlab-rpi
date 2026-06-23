#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# camtest installer
# Thin orchestrator over scripts/setup/* primitives.
#
# Installs camtest as a kiosk that auto-starts on boot (camtest.service): live
# camera preview, sensor selection, and signal-integrity surfacing. For partial
# reconfigures on a dev box, call individual primitives under scripts/setup/.
#
# Usage:
#   sudo ./install.sh                     # full install (interactive port prompt)
#   sudo ./install.sh --port cam0         # non-interactive port
#   sudo ./install.sh --no-readonly       # skip the overlay-root step (Phase 5)
#   ./install.sh --help                   # this message
#
# Requirements:
#   - Raspberry Pi CM5 (or Pi 5) + IO board
#   - Raspberry Pi OS Lite Trixie (64-bit, Debian 13)
#   - Internet connection (apt + git)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/camtest-install.log"
CAMTEST_TAG="install"

# shellcheck source=scripts/common.sh
source "$REPO_DIR/scripts/common.sh"

DO_READONLY=1
PORT_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --no-readonly) DO_READONLY=0 ;;
        --port) die "use --port=cam0 form" ;;
        --port=*) PORT_ARGS=(--port "${arg#*=}") ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

exec > >(tee -a "$LOG_FILE") 2>&1
header "camtest install started at $(date)"
log "Logging to $LOG_FILE"

header "Platform check"
if [ -f /proc/device-tree/model ]; then
    log "Model: $(tr -d '\0' < /proc/device-tree/model)"
fi
ARCH="$(uname -m)"
[ "$ARCH" = "aarch64" ] || die "64-bit OS required (detected: $ARCH)"
log "Architecture: $ARCH"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    log "OS: ${PRETTY_NAME:-unknown}"
fi
log "Install user: $CAMTEST_USER (uid=$CAMTEST_UID)"

# Orchestrate setup primitives. Each is safe to re-run standalone. Order:
# configure everything first, enable the service last. Overlay-root is last of
# all so a partial/interrupted install never leaves a read-only box mid-setup.
"$REPO_DIR/scripts/setup/archive.sh"
"$REPO_DIR/scripts/setup/deps.sh"
"$REPO_DIR/scripts/setup/drivers.sh"
"$REPO_DIR/scripts/setup/config.sh" "${PORT_ARGS[@]}"
"$REPO_DIR/scripts/setup/journald.sh"
"$REPO_DIR/scripts/setup/boot.sh"
"$REPO_DIR/scripts/setup/service.sh" --enable

header "Installing camtestctl command"
ln -sf "$REPO_DIR/scripts/camtestctl.sh" /usr/local/bin/camtestctl
log "Symlinked /usr/local/bin/camtestctl -> $REPO_DIR/scripts/camtestctl.sh"

if [ "$DO_READONLY" -eq 1 ]; then
    "$REPO_DIR/scripts/setup/readonly.sh"
else
    log "Skipping overlay-root (--no-readonly)"
fi

header "Installation complete"
cat <<EOF
camtest installed.

  User:    $CAMTEST_USER
  Service: camtest.service (enabled, auto-starts on boot)
  Repo:    $REPO_DIR

Quick commands:
  sudo reboot                 # boot into the kiosk (loads the sensor overlay)
  camtestctl status           # service state
  camtestctl logs -f          # tail logs
  camtestctl shot             # screenshot the live kiosk
  camtestctl restart          # apply code changes

Install log: $LOG_FILE
EOF
