#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install camlab.service (systemd unit) + its PAM config.
# Safe to re-run. Requires sudo.
#
# Usage:
#   sudo scripts/setup/service.sh             # install unit, do NOT enable at boot
#   sudo scripts/setup/service.sh --enable    # install AND enable at boot (used by install.sh)
#
# Rendered from deploy/camlab.service. PAM gives Cage logind session on tty.

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="service"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

ENABLE_AT_BOOT=0
for arg in "$@"; do
    case "$arg" in
        --enable) ENABLE_AT_BOOT=1 ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

REPO_DIR="$(resolve_repo_dir)"

header "Installing camlab.service"
log "repo=$REPO_DIR user=$CAMLAB_USER uid=$CAMLAB_UID enable-at-boot=$ENABLE_AT_BOOT"

systemctl stop camlab.service 2>/dev/null || true

# Backlight sysfs writes need video group.
if ! id -nG "$CAMLAB_USER" | tr ' ' '\n' | grep -qx video; then
    usermod -aG video "$CAMLAB_USER"
    log "Added $CAMLAB_USER to video group (panel backlight control)"
fi

sed \
    -e "s|CAMLAB_USER|$CAMLAB_USER|g" \
    -e "s|CAMLAB_REPO_DIR|$REPO_DIR|g" \
    "$REPO_DIR/deploy/camlab.service" \
    > /etc/systemd/system/camlab.service
log "Rendered /etc/systemd/system/camlab.service"

CURSOR_DIR=/usr/local/lib/camlab/cursors
python3 "$REPO_DIR/deploy/blank-cursor.py" "$CURSOR_DIR"
log "Wrote blank cursor theme to $CURSOR_DIR (XCURSOR_PATH in the unit)"

cat > /etc/pam.d/camlab <<'PAMEOF'
auth       required pam_unix.so
auth       required pam_env.so
account    required pam_unix.so
session    required pam_unix.so
session    required pam_loginuid.so
session    optional pam_systemd.so
PAMEOF
log "Wrote /etc/pam.d/camlab"

systemctl daemon-reload
log "systemctl daemon-reload"

if [ "$ENABLE_AT_BOOT" -eq 1 ]; then
    systemctl enable camlab.service
    log "Enabled camlab.service for boot"
else
    log "Unit installed but NOT enabled at boot. Use 'sudo systemctl enable camlab.service' to enable."
fi

log "Done."
