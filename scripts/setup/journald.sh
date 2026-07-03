#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Cap journald so logs never fill the rootfs on a long-lived bench box. The GUI
# surfaces camera errors via in-process stderr capture, so we don't rely on the
# journal for the integrity feature - this is just disk hygiene.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/journald.sh

set -euo pipefail

CAMLAB_TAG="journald"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

DROPIN_DIR="/etc/systemd/journald.conf.d"
DROPIN="$DROPIN_DIR/camlab.conf"

header "Configuring journald"

install -d -m 0755 "$DROPIN_DIR"
cat > "$DROPIN" <<'EOF'
# camlab: bound journal size (managed)
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=64M
MaxRetentionSec=2week
EOF
log "Wrote $DROPIN"

systemctl restart systemd-journald
log "Restarted systemd-journald. Done."
