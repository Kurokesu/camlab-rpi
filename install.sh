#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# camlab installer
# Thin orchestrator over scripts/setup/* primitives.
#
# Installs camlab as a kiosk that auto-starts on boot (camlab.service): live
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
LOG_FILE="/var/log/camlab-install.log"
CAMLAB_TAG="install"

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
header "camlab install started at $(date)"
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
log "Install user: $CAMLAB_USER (uid=$CAMLAB_UID)"

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

header "Installing camlabctl command"
ln -sf "$REPO_DIR/scripts/camlabctl.sh" /usr/local/bin/camlabctl
log "Symlinked /usr/local/bin/camlabctl -> $REPO_DIR/scripts/camlabctl.sh"

if [ "$DO_READONLY" -eq 1 ]; then
    "$REPO_DIR/scripts/setup/readonly.sh"
    READONLY_STAGED=1
else
    log "Skipping overlay-root (--no-readonly)"
    READONLY_STAGED=0
fi

header "Installation complete"
cat <<EOF
camlab installed.

  User:    $CAMLAB_USER
  Service: camlab.service (enabled, auto-starts on boot)
  Repo:    $REPO_DIR

Quick commands:
  sudo reboot                 # boot into the kiosk (loads the sensor overlay)
  camlabctl status           # service state
  camlabctl logs -f          # tail logs
  camlabctl shot             # screenshot the live kiosk
  camlabctl restart          # apply code changes

Install log: $LOG_FILE
EOF

if [ "$READONLY_STAGED" -eq 1 ]; then
    cat <<'EOF'

Read-only root: your reboot triggers one more automatic reboot to lock it in.
Dev toggle: camlabctl rw / ro.
EOF
fi
