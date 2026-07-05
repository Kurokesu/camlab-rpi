#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# camlab installer
# Thin orchestrator over scripts/setup/* primitives.
#
# Installs camlab to /opt/camlab as a kiosk that auto-starts on boot
# (camlab.service). For partial reconfigures, call individual primitives
# under scripts/setup/.
#
# Usage:
#   sudo ./install.sh                     # full install (port defaults to cam1)
#   sudo ./install.sh --port=cam0         # override CSI port to cam0
#   sudo ./install.sh --no-readonly       # keep root fs writable (dev install)
#   ./install.sh --help                   # this message
#
# Requirements:
#   - Raspberry Pi CM5 + IO board, or a Pi 5
#   - Raspberry Pi OS Lite Trixie (64-bit, Debian 13)
#   - Internet connection (apt)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/camlab-install.log"
CAMLAB_TAG="install"

# Dev-clone clutter pruned from the /opt/camlab copy. Release zips
# (git archive) don't have it.
DEV_CLUTTER=(.git .github .venv .gitignore .shellcheckrc .gitattributes pyproject.toml docs)

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
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
case "$MODEL" in
    "Raspberry Pi"*) log "Model: $MODEL" ;;
    *) die "Raspberry Pi required (detected: ${MODEL:-unknown hardware})" ;;
esac
ARCH="$(uname -m)"
[ "$ARCH" = "aarch64" ] || die "64-bit OS required (detected: $ARCH)"
log "Architecture: $ARCH"
[ -f /etc/os-release ] || die "cannot detect OS (/etc/os-release missing)"
. /etc/os-release
log "OS: ${PRETTY_NAME:-unknown}"
[ "${VERSION_CODENAME:-}" = "trixie" ] \
    || die "unsupported OS: ${PRETTY_NAME:-unknown} (need Raspberry Pi OS Lite Trixie, 64-bit)"
# Desktop images boot to graphical.target and the session would fight the
# kiosk for tty1. Lite boots to multi-user.target.
if [ "$(systemctl get-default)" = "graphical.target" ]; then
    die "Raspberry Pi OS Desktop detected. Use the Lite image."
fi
log "Install user: $CAMLAB_USER (uid=$CAMLAB_UID)"

# Fixed app location. Re-run from $APP_DIR itself skips the copy.
# Stage the copy, then swap. A failed copy leaves the old tree in place.
APP_DIR="/opt/camlab"
if [ "$REPO_DIR" != "$APP_DIR" ]; then
    header "Installing app to $APP_DIR"
    STAGE_DIR="$APP_DIR.new"
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"
    cp -a "$REPO_DIR/." "$STAGE_DIR/"
    for item in "${DEV_CLUTTER[@]}"; do
        rm -rf "${STAGE_DIR:?}/$item"
    done
    find "$STAGE_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
    chown -R root:root "$STAGE_DIR"
    rm -rf "$APP_DIR"
    mv "$STAGE_DIR" "$APP_DIR"
    log "Copied $REPO_DIR -> $APP_DIR"
fi
# Precompile, the service user cannot write bytecode into the root-owned tree.
python3 -m compileall -q -j 0 "$APP_DIR/camlab"

# Setup primitives run from $APP_DIR so every rendered path points there.
# Overlay-root last, so a partial install never leaves a read-only box.
"$APP_DIR/scripts/setup/deps.sh"
"$APP_DIR/scripts/setup/drivers.sh"
"$APP_DIR/scripts/setup/config.sh" "${PORT_ARGS[@]}"
"$APP_DIR/scripts/setup/journald.sh"
"$APP_DIR/scripts/setup/boot.sh"
"$APP_DIR/scripts/setup/service.sh" --enable

header "Installing camlabctl command"
ln -sf "$APP_DIR/scripts/camlabctl.sh" /usr/local/bin/camlabctl
log "Symlinked /usr/local/bin/camlabctl -> $APP_DIR/scripts/camlabctl.sh"

if [ "$DO_READONLY" -eq 1 ]; then
    "$APP_DIR/scripts/setup/readonly.sh"
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
  App:     $APP_DIR

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
