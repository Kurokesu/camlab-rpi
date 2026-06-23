#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Install camtest.service (systemd unit) + its PAM config.
# Safe to re-run. Requires sudo.
#
# Usage:
#   sudo scripts/setup/service.sh             # install unit, do NOT enable at boot
#   sudo scripts/setup/service.sh --enable    # install AND enable at boot (used by install.sh)
#
# The unit is rendered from deploy/camtest.service. Cage needs a valid logind
# session on the tty, which the PAM stack (pam_systemd) provides.

set -euo pipefail

CAMTEST_TAG="service"

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

header "Installing camtest.service"
log "repo=$REPO_DIR user=$CAMTEST_USER uid=$CAMTEST_UID enable-at-boot=$ENABLE_AT_BOOT"

systemctl stop camtest.service 2>/dev/null || true

sed \
    -e "s|CAMTEST_USER|$CAMTEST_USER|g" \
    -e "s|CAMTEST_REPO_DIR|$REPO_DIR|g" \
    "$REPO_DIR/deploy/camtest.service" \
    > /etc/systemd/system/camtest.service
log "Rendered /etc/systemd/system/camtest.service"

cat > /etc/pam.d/camtest <<'PAMEOF'
auth       required pam_unix.so
auth       required pam_env.so
account    required pam_unix.so
session    required pam_unix.so
session    required pam_loginuid.so
session    optional pam_systemd.so
PAMEOF
log "Wrote /etc/pam.d/camtest"

systemctl daemon-reload
log "systemctl daemon-reload"

if [ "$ENABLE_AT_BOOT" -eq 1 ]; then
    systemctl enable camtest.service
    log "Enabled camtest.service for boot"
else
    log "Unit installed but NOT enabled at boot. Use 'sudo systemctl enable camtest.service' to enable."
fi

log "Done."
